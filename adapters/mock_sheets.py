"""Durable offline workbook simulator. Never implements Google credentials or HTTP."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from application.tasks import StateConflict
from domain.snapshots import canonical_json
from domain.layout import Column, Layout, LayoutPlan, confirm_layout


class MockSheets:
    """One atomic generated-area replacement per workbook; human inputs are separate."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.write_count = 0

    def create(self, resource: str, layout_revision: str, inputs: dict) -> None:
        path = self._path(resource)
        if path.exists():
            raise StateConflict("mock workbook exists")
        self._save(resource, {"layout_revision": layout_revision, "inputs": inputs,
                              "generated": {}, "commit_id": None, "payload_hash": None})

    def read(self, resource: str) -> dict:
        return json.loads(self._path(resource).read_text(encoding="utf-8"))

    def edit_inputs(self, resource: str, inputs: dict) -> None:
        book = self.read(resource)
        book["inputs"] = copy.deepcopy(inputs)
        self._save(resource, book)

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

    def apply_layout(self, plan: LayoutPlan, confirmed_plan_id: str, config_id: str, code_sha: str) -> None:
        current = self.read_layout(plan.before.resource)
        updated = confirm_layout(plan, current, confirmed_plan_id, config_id, code_sha)
        book = self.read(current.resource)
        book["layout_archives"].append({"layout": asdict(current), "generated": copy.deepcopy(book["generated"]),
                                         "plan_id": plan.plan_id})
        book["layout"] = asdict(updated)
        book["layout_revision"] = updated.revision
        self._save(current.resource, book)

    def apply(self, resource: str, revision: str, generated: dict, commit_id: str,
              fault: str | None = None) -> None:
        if fault not in (None, "before", "after"):
            raise ValueError("unsupported mock failure")
        book = self.read(resource)
        if book["layout_revision"] != revision:
            raise StateConflict("workbook layout changed")
        payload_hash = _hash(generated)
        if book["commit_id"] == commit_id:
            if book["payload_hash"] != payload_hash or _hash(book["generated"]) != payload_hash:
                raise StateConflict("commit content differs")
            return
        if fault == "before":
            raise TimeoutError("mock request outcome unknown before application")
        book.update(generated=copy.deepcopy(generated), commit_id=commit_id, payload_hash=payload_hash)
        self._save(resource, book)
        self.write_count += 1
        if fault == "after":
            raise TimeoutError("mock response lost after application")

    def observe(self, resource: str, generated: dict, commit_id: str) -> str | None:
        book = self.read(resource)
        expected = _hash(generated)
        if (book["commit_id"] == commit_id and book["payload_hash"] == expected
                and _hash(book["generated"]) == expected):
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
