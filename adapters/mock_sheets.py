"""Durable offline workbook simulator. Never implements Google credentials or HTTP."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from adapters.mock_workbook_lock import locked_workbook_access
from application.tasks import StateConflict
from application.publication_safety import capture_publication_state
from adapters.mock_grid import GridMigrationPlan, is_same_grid_layout, materialize_generated_grid, prepare_grid_migration
from adapters.legacy_migration import SOURCE_KEYS, LegacyMigrationPlan, confirm_legacy_migration
from domain.snapshots import canonical_json
from domain.layout import Column, Layout, LayoutPlan, confirm_layout, encode_cell_value


class MockSheets:
    """One atomic generated-area replacement per workbook; human inputs are separate."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.write_count = 0

    @locked_workbook_access
    def create(self, resource: str, layout_revision: str, inputs: dict) -> None:
        path = self._path(resource)
        if path.exists():
            raise StateConflict("mock workbook exists")
        self._save(resource, {"layout_revision": layout_revision, "inputs": inputs,
                              "generated": {}, "commit_id": None, "payload_hash": None})

    @locked_workbook_access
    def read(self, resource: str) -> dict:
        return json.loads(self._path(resource).read_text(encoding="utf-8"))

    def capture_publication_state(self, resource, publication_id, commit_id, result_id, code_sha):
        try:
            book = self.read(resource)
        except FileNotFoundError:
            owned = {"is_missing": True}
        else:
            generated, grid = book["generated"], book.get("generated_grid")
            if ((book["commit_id"] is None and (generated != {} or book["payload_hash"] is not None or grid is not None))
                    or book["commit_id"] is not None and (book["payload_hash"] != _hash(generated)
                        or canonical_json(grid) != canonical_json(materialize_generated_grid(generated)))):
                raise StateConflict("workbook baseline content is inconsistent")
            owned = {"is_missing":False,"layout_revision":book["layout_revision"],"commit_id":book["commit_id"],
                "payload_hash":book["payload_hash"],"generated_hash":_hash(generated),"grid_hash":_hash(grid),
                "layout_hash":_hash(book.get("layout"))}
        return capture_publication_state(resource,publication_id,commit_id,result_id,code_sha,owned)

    @locked_workbook_access
    def edit_inputs(self, resource: str, inputs: dict) -> None:
        book = self.read(resource)
        book["inputs"] = copy.deepcopy(inputs)
        self._save(resource, book)

    @locked_workbook_access
    def set_manual_tab(self, resource: str, tab_key: str, title: str, rows: list) -> None:
        book = self.read(resource)
        generated_tabs = (book.get("generated_grid") or {}).get("tabs", {})
        manual = book.setdefault("manual_tabs", {})
        if (not isinstance(tab_key, str) or not tab_key.strip() or not isinstance(title, str) or not title.strip()
                or tab_key in generated_tabs or any(tab["title"].casefold() == title.casefold() for tab in generated_tabs.values())
                or any(key != tab_key and tab["title"].casefold() == title.casefold() for key, tab in manual.items())):
            raise StateConflict("manual tab ownership or title conflicts")
        if (not isinstance(rows, list) or not 1 <= len(rows) <= 10000 or not isinstance(rows[0], list)
                or not 1 <= len(rows[0]) <= 10000 or len(rows)*len(rows[0]) > 500000
                or any(not isinstance(row, list) or len(row) != len(rows[0]) for row in rows)):
            raise ValueError("invalid bounded manual grid")
        manual[tab_key] = {"title": title, "cells": [[{"value": encode_cell_value(cell)} for cell in row] for row in rows]}
        self._save(resource, book)

    @locked_workbook_access
    def edit_cell(self, resource: str, tab_key: str, row: int, column: int, value: object) -> None:
        book = self.read(resource)
        if tab_key in (book.get("generated_grid") or {}).get("tabs", {}):
            raise StateConflict("generated tab is protected")
        tab = book.get("manual_tabs", {}).get(tab_key)
        if (tab is None or type(row) is not int or type(column) is not int
                or not 0 <= row < len(tab["cells"]) or not 0 <= column < len(tab["cells"][row])):
            raise ValueError("manual cell is outside grid")
        tab["cells"][row][column] = {"value": encode_cell_value(value)}
        self._save(resource, book)

    def plan_grid_migration(self, resource: str, generated: dict) -> GridMigrationPlan:
        return prepare_grid_migration(resource, self.read(resource), generated)

    @locked_workbook_access
    def approve_grid_migration(self, plan: GridMigrationPlan, generated: dict, confirmed_plan_id: str,
                               config_id: str, code_sha: str) -> None:
        book = self.read(plan.resource)
        current = prepare_grid_migration(plan.resource, book, generated)
        if (current != plan or plan.plan_id != confirmed_plan_id
                or config_id != plan.config_id or code_sha != plan.code_sha):
            raise StateConflict("grid migration confirmation or dependencies differ")
        if len(book.get("grid_archives", [])) >= 20:
            raise StateConflict("grid migration archive capacity reached")
        book["grid_migration_approval"] = {"plan_id": plan.plan_id, "plan": asdict(plan)}
        self._save(plan.resource, book)

    @locked_workbook_access
    def apply_legacy_migration(self, plan: LegacyMigrationPlan, catalog, confirmed_plan_id: str,
                                config_id: str, code_sha: str) -> str:
        book = self.read(plan.resource)
        if (plan.plan_id != confirmed_plan_id or config_id != plan.config_id or code_sha != plan.code_sha
                or _hash(asdict(catalog)) != plan.catalog_hash):
            raise StateConflict("legacy migration confirmation differs")
        archives = book.get("legacy_standard_archives", [])
        if any(archive["plan_id"] == plan.plan_id for archive in archives):
            current = {key: book["inputs"][key] for key in SOURCE_KEYS if key in book["inputs"]}
            if canonical_json(current) != plan.target_inputs_json or book["layout_revision"] != plan.target_layout_revision:
                raise StateConflict("legacy migration replay source changed")
            return book["layout_revision"]
        if "layout" in book or len(archives) >= 20:
            raise StateConflict("legacy migration layout ownership or archive capacity differs")
        updated = confirm_legacy_migration(plan, book, catalog, confirmed_plan_id, config_id, code_sha)
        archived = {key: copy.deepcopy(book["inputs"][key]) for key in SOURCE_KEYS if key in book["inputs"]}
        book.setdefault("legacy_standard_archives", []).append({"plan_id": plan.plan_id,
            "layout_revision": book["layout_revision"], "inputs": archived})
        book["inputs"].update(updated)
        book["layout_revision"] = plan.target_layout_revision
        self._save(plan.resource, book)
        return book["layout_revision"]

    @locked_workbook_access
    def initialize_layout(self, layout: Layout) -> None:
        book = self.read(layout.resource)
        if "layout" in book:
            raise StateConflict("layout already initialized")
        book["layout"] = asdict(layout)
        book["layout_revision"] = layout.revision
        book["layout_archives"] = []
        self._save(layout.resource, book)

    def read_layout(self, resource: str) -> Layout:
        data = self.read(resource)["layout"]
        return Layout(data["resource"], data["tab_id"], tuple(Column(**c) for c in data["columns"]), data["schema_version"])

    @locked_workbook_access
    def apply_layout(self, plan: LayoutPlan, confirmed_plan_id: str, config_id: str, code_sha: str) -> None:
        current = self.read_layout(plan.before.resource)
        updated = confirm_layout(plan, current, confirmed_plan_id, config_id, code_sha)
        book = self.read(current.resource)
        book["layout_archives"].append({"layout": asdict(current), "generated": copy.deepcopy(book["generated"]),
                                         "plan_id": plan.plan_id})
        book["layout"] = asdict(updated)
        book["layout_revision"] = updated.revision
        self._save(current.resource, book)

    @locked_workbook_access
    def apply(self, resource: str, revision: str, generated: dict, commit_id: str,
              fault: str | None = None) -> None:
        if fault not in (None, "before", "after"):
            raise ValueError("unsupported mock failure")
        book = self.read(resource)
        if book["layout_revision"] != revision:
            raise StateConflict("workbook layout changed")
        payload_hash = _hash(generated)
        grid = materialize_generated_grid(generated)
        if book["commit_id"] == commit_id:
            if (book["payload_hash"] != payload_hash or _hash(book["generated"]) != payload_hash
                    or canonical_json(book.get("generated_grid")) != canonical_json(grid)):
                raise StateConflict("commit content differs")
            return
        previous = book.get("generated_grid")
        if previous is not None and (grid is None or not is_same_grid_layout(previous, grid)):
            approval = book.get("grid_migration_approval")
            if grid is None or approval is None:
                raise StateConflict("generated grid layout migration required")
            plan = prepare_grid_migration(resource, book, generated)
            if (approval.get("plan_id") != plan.plan_id or canonical_json(approval.get("plan")) != canonical_json(asdict(plan))
                    or len(book.get("grid_archives", [])) >= 20):
                raise StateConflict("generated grid migration approval is stale")
            book.setdefault("grid_archives", []).append({"plan_id": plan.plan_id, "commit_id": book["commit_id"],
                "payload_hash": book["payload_hash"], "generated": book["generated"], "generated_grid": previous})
        if grid is not None:
            manual = book.get("manual_tabs", {})
            if (set(manual) & set(grid["tabs"]) or {tab["title"].casefold() for tab in manual.values()}
                    & {tab["title"].casefold() for tab in grid["tabs"].values()}):
                raise StateConflict("generated grid conflicts with manual tab")
        if fault == "before":
            raise TimeoutError("mock request outcome unknown before application")
        book.update(generated=copy.deepcopy(generated), commit_id=commit_id, payload_hash=payload_hash)
        if grid is not None:
            book["generated_grid"] = grid
        book.pop("grid_migration_approval", None)
        self._save(resource, book)
        self.write_count += 1
        if fault == "after":
            raise TimeoutError("mock response lost after application")

    def observe(self, resource: str, generated: dict, commit_id: str) -> str | None:
        book = self.read(resource)
        expected = _hash(generated)
        if (book["commit_id"] == commit_id and book["payload_hash"] == expected
                and _hash(book["generated"]) == expected
                and canonical_json(book.get("generated_grid")) == canonical_json(materialize_generated_grid(generated))):
            return commit_id
        return None

    def _path(self, resource: str) -> Path:
        if not isinstance(resource, str) or not resource:
            raise ValueError("invalid mock resource")
        return self.directory / (hashlib.sha256(resource.encode()).hexdigest() + ".json")

    def _save(self, resource: str, book: dict) -> None:
        path = self._path(resource)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(canonical_json(book), encoding="utf-8")
        temporary.replace(path)


def _hash(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
