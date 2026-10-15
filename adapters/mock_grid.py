"""Deterministic physical grid projection for the offline workbook simulator."""

from domain.view_grid import restore_workbook_view


def materialize_generated_grid(generated: dict) -> dict | None:
    if "view" not in generated:
        if "view_id" in generated:
            raise ValueError("view identity without view")
        return None
    view = restore_workbook_view(generated["view"])
    if (generated.get("view_id") != view.view_id
            or generated.get("config_id") != view.config_id
            or generated.get("snapshot_id") != view.training_id
            or generated.get("calculation_id") != view.calculation_id
            or generated.get("code_sha") != view.code_sha):
        raise ValueError("generated grid source binding differs")
    return {"schema_version": 1, "view_id": view.view_id, "tabs": {
        tab.tab_key: {"title": tab.title, "layout_revision": tab.layout_revision,
            "row_count": len(tab.rows), "column_count": len(tab.column_ids),
            "column_ids": tab.column_ids, "row_ids": tab.row_ids, "cells": tab.typed_rows(),
            "merges": tab.merges, "frozen_rows": tab.frozen_rows,
            "frozen_columns": tab.frozen_columns, "hidden_columns": tab.hidden_columns,
            "is_generated_protected": True}
        for tab in view.tabs}}


def is_same_grid_layout(before: dict, after: dict) -> bool:
    return {key: tab["layout_revision"] for key, tab in before["tabs"].items()} == {
        key: tab["layout_revision"] for key, tab in after["tabs"].items()}
