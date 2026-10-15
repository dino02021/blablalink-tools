"""Whitelist raw API fields and normalize immutable offline collection evidence."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from domain.snapshots import Document, MemberKey, TrainingRecord, capture_document, capture_training

OUTCOMES = frozenset({"complete", "partial", "failed", "auth_required"})
DETAIL_FIELDS = ("name_code", "skill1_lv", "skill2_lv", "ulti_skill_lv", "favorite_item_tid", "favorite_item_lv",
                 *(f"{part}_equip_{field}" for part in ("head", "arm", "torso", "leg") for field in ("tier", "lv")))


@dataclass(frozen=True)
class NormalizedCollection:
    training: Document
    issues: tuple[tuple[str, str, str], ...]
    missing_details: tuple[tuple[str, str], ...]
    is_auth_required: bool


def capture_raw_training(scope_id: str, captured_at: str, reference_version: str, members: tuple[dict, ...],
                         role_ids: tuple[str, ...] | None = None) -> Document:
    """Only known data fields survive; caller transport headers/session data never enter raw."""
    cleaned, identities = [], set()
    for member in members:
        identity = MemberKey(member["area_id"], member["member_id"])
        if identity.key in identities:
            raise ValueError("duplicate collected member")
        identities.add(identity.key)
        ownership, details = member["ownership"], member["details"]
        for source in (ownership, details):
            if source.get("status") not in OUTCOMES:
                raise ValueError("explicit source outcome required")
        characters = [_select(row, ("name_code", "grade", "core")) for row in _rows(ownership.get("characters", []))]
        rows = []
        for row in _rows(details.get("character_details", [])):
            fields = (*DETAIL_FIELDS, *(key for key in row if re.fullmatch(r"equip_option_?[0-9]+", key)))
            rows.append(_select(row, fields))
        effects = []
        for row in _rows(details.get("state_effects", [])):
            effects.append({"id": row.get("id"), "function_details": [
                _select(item, ("function_id", "function_value")) for item in _rows(row.get("function_details", []))]})
        requested = details.get("requested_codes", [])
        if not isinstance(requested, (list, tuple)) or len(set(requested)) != len(requested):
            raise ValueError("unique detail request scope required")
        for code in requested:
            _identifier(code)
        if type(details.get("is_options_complete", False)) is not bool:
            raise ValueError("explicit option coverage flag must be boolean")
        cleaned.append({"area_id": identity.area_id, "member_id": identity.member_id,
            "ownership": {"status": ownership["status"], "characters": characters},
            "details": {"status": details["status"], "requested_codes": list(requested),
                "is_options_complete": details.get("is_options_complete", False),
                "character_details": rows, "state_effects": effects}})
    payload = {"source_schema_version": 1, "members": cleaned}
    if role_ids is not None:
        if isinstance(role_ids, str):
            raise ValueError("role universe must be an identity sequence")
        role_ids = tuple(role_ids)
        if not role_ids or len(role_ids) > 1000 or len(set(role_ids)) != len(role_ids) or any(not isinstance(code, str) or not code.strip() for code in role_ids):
            raise ValueError("bounded unique source role universe required")
        if any({str(row["name_code"]) for row in member["ownership"]["characters"]} - set(role_ids)
                or set(member["details"]["requested_codes"]) - set(role_ids) for member in cleaned):
            raise ValueError("source row is outside role universe")
        payload["reference_role_ids"] = list(role_ids)
    return capture_document("raw", scope_id, captured_at, reference_version, payload)


def normalize_training(raw: Document, role_ids: tuple[str, ...], function_prefixes: dict[str, str]) -> NormalizedCollection:
    envelope, payload = raw.envelope, raw.payload
    if (envelope["kind"] != "raw" or type(payload.get("source_schema_version")) is not int
            or payload["source_schema_version"] != 1):
        raise ValueError("unsupported raw training schema")
    role_ids = tuple(role_ids)
    if "reference_role_ids" in payload and payload["reference_role_ids"] != list(role_ids):
        raise ValueError("normalization role universe differs from source coverage")
    if not role_ids or len(set(role_ids)) != len(role_ids):
        raise ValueError("unique reference roles required")
    for code in role_ids:
        if not isinstance(code, str):
            raise ValueError("reference role identities must be strings")
        _identifier(code)
    for prefix, function_id in function_prefixes.items():
        if not isinstance(prefix, str) or len(prefix) != 5 or not prefix.isascii() or not prefix.isdigit():
            raise ValueError("function prefix requires five ASCII digits")
        _identifier(function_id)
    if len(set(function_prefixes.values())) != len(function_prefixes):
        raise ValueError("duplicate function reference identity")
    records, issues, missing, identities = [], [], [], set()
    is_auth_required = False
    for member in _rows(payload.get("members")):
        identity = MemberKey(member["area_id"], member["member_id"])
        if identity.key in identities:
            raise ValueError("duplicate raw member")
        identities.add(identity.key)
        ownership, details = member["ownership"], member["details"]
        if ownership["status"] not in OUTCOMES or details["status"] not in OUTCOMES:
            raise ValueError("invalid source outcome")
        is_auth_required |= "auth_required" in (ownership["status"], details["status"])
        owned = _index(ownership["characters"], "name_code")
        detailed = _index(details["character_details"], "name_code")
        requested = set(details["requested_codes"])
        if not set(detailed) <= requested or not (set(owned) | requested) <= set(role_ids):
            raise ValueError("response or request outside pinned role reference")
        effects = _index(details["state_effects"], "id")
        if ownership["status"] in ("failed", "auth_required") and owned:
            raise ValueError("failed ownership source cannot assert owned roles")
        if details["status"] in ("failed", "auth_required") and detailed:
            raise ValueError("failed detail source cannot assert metrics")
        for role in role_ids:
            source = owned.get(role)
            status = "owned" if source else "not_owned" if ownership["status"] == "complete" else "unknown"
            detail = detailed.get(role)
            if detail and status == "not_owned":
                raise ValueError("ownership and detail sources conflict")
            metrics, traits = {}, {}
            if status == "owned":
                grade, core = _integer(source.get("grade")), _integer(source.get("core"))
                metrics["breakthrough"] = grade + core if grade is not None and core is not None else None
                if detail:
                    metrics.update(_metrics(detail))
                    if type(details["is_options_complete"]) is not bool:
                        raise ValueError("invalid option coverage flag")
                    traits, trait_issues = _traits(detail, effects, function_prefixes,
                        details["status"] == "complete" and details["is_options_complete"])
                    issues.extend((identity.key, role, issue) for issue in trait_issues)
                else:
                    missing.append((identity.key, role))
                    issues.append((identity.key, role, "details_missing"))
            elif status == "unknown":
                issues.append((identity.key, role, "ownership_unknown"))
            records.append(TrainingRecord(identity, role, status, metrics, traits))
    training = capture_training(envelope["scope_id"], envelope["captured_at"], envelope["reference_version"], tuple(records))
    training = capture_document("training", envelope["scope_id"], envelope["captured_at"], envelope["reference_version"],
        {**training.payload, "raw_snapshot_id": raw.snapshot_id, "normalizer_version": 1})
    return NormalizedCollection(training, tuple(issues), tuple(missing), is_auth_required)


def _metrics(detail: dict) -> dict:
    metrics = {target: _integer(detail.get(source)) for target, source in (
        ("skill_1", "skill1_lv"), ("skill_2", "skill2_lv"), ("skill_burst", "ulti_skill_lv"))}
    for part in ("head", "arm", "torso", "leg"):
        tier, level = _integer(detail.get(f"{part}_equip_tier")), _integer(detail.get(f"{part}_equip_lv"))
        metrics[f"equip_{part}"] = -1 if tier == 0 else "T9" if tier == 9 else level if tier is not None else None
    tid = str(detail.get("favorite_item_tid") or "")
    level = _integer(detail.get("favorite_item_lv"))
    if tid.startswith("2"):
        collection = "SR15"
    elif tid.endswith(("1", "2")) and level is not None and 0 <= level <= 15:
        collection = ("R" if tid.endswith("1") else "SR") + str(level)
    elif tid.endswith("0"):
        collection = 0  # explicit N: no collectible; not unknown
    else:
        collection = None
    metrics["collection"] = collection
    return metrics


def _traits(detail: dict, effects: dict, prefixes: dict, is_complete: bool) -> tuple[dict, tuple[str, ...]]:
    values = {fid: 0.0 for fid in prefixes.values()} if is_complete else {}
    issues, unknown = [], set()
    for key, option in detail.items():
        if not key.startswith("equip_option") or option in (None, "", 0, "0"):
            continue
        option = str(option)
        function_id = prefixes.get(option[:5])
        if function_id is None:
            issues.append("unmapped_function_prefix")
            continue
        effect = effects.get(option)
        entries = effect.get("function_details", []) if effect else []
        value = _number(entries[0].get("function_value")) if entries else None
        if value is None:
            unknown.add(function_id)
            issues.append("state_effect_missing_or_invalid")
        else:
            values[function_id] = values.get(function_id, 0.0) + abs(value / 100.0)
    for fid in unknown:
        values[fid] = None
    if not is_complete:
        values = {fid: None for fid in prefixes.values()}
        issues.append("option_coverage_incomplete")
    return {fid: round(value, 2) if value is not None else None for fid, value in values.items()}, tuple(issues)


def _number(value):
    if type(value) not in (int, float, str) or isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, str) and not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value.strip()):
        return None
    try:
        parsed = float(value)
    except (ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value):
    parsed = _number(value)
    return int(parsed) if parsed is not None and 0 <= parsed <= 2 ** 53 and parsed.is_integer() else None


def _select(row: dict, fields) -> dict:
    selected = {key: row[key] for key in fields if key in row}
    if any(value is not None and type(value) not in (str, int, float, bool) for value in selected.values()):
        raise ValueError("raw scalar field contains unexpected nested data")
    return selected


def _rows(value) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("expected explicit source rows")
    return value


def _index(rows: list, key: str) -> dict:
    indexed = {}
    for row in _rows(rows):
        identity = _identifier(row.get(key))
        if identity in indexed:
            raise ValueError("duplicate source identity")
        indexed[identity] = row
    return indexed


def _identifier(value) -> str:
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("stable source identity required")
    return value
