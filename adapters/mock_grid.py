"""Deterministic physical grid projection for the offline workbook simulator."""

from dataclasses import asdict, dataclass
import hashlib

from domain.snapshots import canonical_json
from domain.view_grid import restore_workbook_view
from application.raid_workbook import restore_raid_workbook


@dataclass(frozen=True)
class GridMigrationPlan:
    resource: str
    layout_revision: str
    base_commit_id: str
    base_payload_hash: str
    base_grid_hash: str
    target_payload_hash: str
    target_grid_hash: str
    target_view_id: str
    config_id: str
    code_sha: str
    changes: tuple[tuple[str, str], ...]

    @property
    def plan_id(self) -> str:
        return _hash(asdict(self))


def materialize_generated_grid(generated: dict) -> dict | None:
    if generated.get("publication_kind") == "confirmed_raid":
        if "view" in generated:
            raise ValueError("ambiguous generated view kind")
        view = restore_raid_workbook(generated["raid_workbook_view"])
        if (generated.get("view_id") != view.view_id or generated.get("problem_id") != view.problem_id
                or generated.get("draft_plan_id") != view.draft_plan_id or generated.get("code_sha") != view.code_sha
                or not generated.get("confirmation_id")):
            raise ValueError("confirmed raid grid source binding differs")
        return _materialize_tabs(view)
    if "raid_workbook_view" in generated:
        raise ValueError("raid view requires explicit publication kind")
    if "view" not in generated:
        if "view_id" in generated:
            raise ValueError("view identity without view")
        return None
    view = restore_workbook_view(generated["view"])
    if len({tab.title.casefold() for tab in view.tabs}) != len(view.tabs):
        raise ValueError("generated tab titles collide")
    if (generated.get("view_id") != view.view_id
            or generated.get("config_id") != view.config_id
            or generated.get("snapshot_id") != view.training_id
            or generated.get("calculation_id") != view.calculation_id
            or generated.get("code_sha") != view.code_sha):
        raise ValueError("generated grid source binding differs")
    return _materialize_tabs(view)


def is_same_grid_layout(before: dict, after: dict) -> bool:
    return {key: tab["layout_revision"] for key, tab in before["tabs"].items()} == {
        key: tab["layout_revision"] for key, tab in after["tabs"].items()}


def prepare_grid_migration(resource: str, book: dict, generated: dict) -> GridMigrationPlan:
    before = book.get("generated_grid")
    after = materialize_generated_grid(generated)
    if (not isinstance(resource, str) or not resource.strip() or before is None or after is None
            or not book.get("commit_id") or book.get("payload_hash") != _hash(book["generated"])
            or canonical_json(before) != canonical_json(materialize_generated_grid(book["generated"]))):
        raise ValueError("grid migration requires a verified existing view")
    if is_same_grid_layout(before, after):
        raise ValueError("grid structure is unchanged")
    changes = []
    for key in sorted(set(before["tabs"]) | set(after["tabs"])):
        if key not in before["tabs"]:
            changes.append(("add", key))
        elif key not in after["tabs"]:
            changes.append(("archive", key))
        elif before["tabs"][key]["layout_revision"] != after["tabs"][key]["layout_revision"]:
            changes.append(("replace", key))
    return GridMigrationPlan(resource, book["layout_revision"], book["commit_id"], book["payload_hash"],
        _hash(before), _hash(generated), _hash(after), after["view_id"], generated["config_id"], generated["code_sha"], tuple(changes))


def _materialize_tabs(view):
    return {"schema_version": 1, "view_id": view.view_id, "tabs": {
        tab.tab_key: {"title": tab.title, "layout_revision": tab.layout_revision,
            "row_count": len(tab.rows), "column_count": len(tab.column_ids),
            "column_ids": tab.column_ids, "row_ids": tab.row_ids, "cells": tab.typed_rows(),
            "merges": tab.merges, "frozen_rows": tab.frozen_rows,
            "frozen_columns": tab.frozen_columns, "hidden_columns": tab.hidden_columns,
            "is_generated_protected": True}
        for tab in view.tabs}}



def _hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
