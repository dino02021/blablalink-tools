"""Preview complete roster transitions without losing identity-keyed human inputs."""

import copy
from dataclasses import asdict, dataclass
import hashlib
import re

from domain.member_epochs import validate_member_epochs
from domain.snapshots import Document, MemberKey, Override, canonical_json, capture_document


@dataclass(frozen=True)
class RosterMember:
    member: MemberKey
    display_name: str

    def __post_init__(self):
        if not isinstance(self.member, MemberKey) or not isinstance(self.display_name, str) or len(self.display_name) > 512:
            raise ValueError("bounded roster identity and display name required")


@dataclass(frozen=True)
class RosterChangePlan:
    base_inputs_id: str
    observation_id: str
    code_sha: str
    order_policy: str
    added: tuple[MemberKey, ...]
    removed: tuple[MemberKey, ...]
    renamed: tuple[tuple[MemberKey, str, str], ...]
    rejoined: tuple[MemberKey, ...]
    blockers: tuple[str, ...]
    target_inputs: Document | None
    is_noop: bool

    @property
    def plan_id(self):
        return _hash(asdict(self))


def capture_roster_observation(scope_id, captured_at, reference_version, members, source_status, source_revision):
    members = tuple(members)
    if (not isinstance(source_status, str) or source_status not in ("complete", "partial", "failed", "auth_required")
            or not isinstance(source_revision, str) or not 1 <= len(source_revision) <= 512
            or len(members) > 300 or any(not isinstance(member, RosterMember) for member in members)
            or len({member.member for member in members}) != len(members)
            or source_status in ("failed", "auth_required") and members):
        raise ValueError("explicit unique roster observation required")
    return capture_document("union_inputs", scope_id, captured_at, reference_version,
        {"input_kind": "roster_observation", "roster_schema_version": 1, "source_status": source_status,
            "source_revision_hash": _hash(source_revision), "members": [asdict(member) for member in members]})


def prepare_roster_change(current_inputs, observation, code_sha, order_policy="preserve_existing"):
    if (current_inputs.envelope["kind"] != "union_inputs" or observation.envelope["kind"] != "union_inputs"
            or current_inputs.envelope["scope_id"] != observation.envelope["scope_id"]
            or not isinstance(code_sha, str) or not code_sha.strip() or order_policy not in ("preserve_existing", "observed")
            or len(current_inputs.serialized.encode()) > 8*1024*1024):
        raise ValueError("fixed bounded roster change inputs required")
    data = observation.payload
    if (set(data) != {"input_kind", "roster_schema_version", "source_status", "source_revision_hash", "members"}
            or data["input_kind"] != "roster_observation" or type(data["roster_schema_version"]) is not int or data["roster_schema_version"] != 1
            or not isinstance(data["source_revision_hash"], str) or re.fullmatch("[a-f0-9]{64}", data["source_revision_hash"]) is None):
        raise ValueError("unknown roster observation schema")
    observed = _member_rows(data["members"], is_observation=True)
    status = data["source_status"]
    if status not in ("complete", "partial", "failed", "auth_required") or status in ("failed", "auth_required") and observed:
        raise ValueError("invalid roster source outcome")
    payload = current_inputs.payload
    current = _member_rows(payload["members"])
    if "roster_state" in payload and payload["roster_state"] != ("active" if current else "inactive"):
        raise ValueError("roster state contradicts membership")
    epochs = validate_member_epochs(payload.get("member_epochs"), (MemberKey(**row["member"]) for row in current.values()))
    manual = payload.get("manual_member_values", {})
    if not isinstance(manual, dict) or set(manual) - set(current):
        raise ValueError("human values require explicit current member identities")
    overrides, seen = [], set()
    for row in payload.get("overrides", []):
        override = Override(**{**row, "member": MemberKey(**row["member"])})
        if override.member.key not in current or override.key in seen:
            raise ValueError("override identity is absent or duplicated")
        seen.add(override.key)
        overrides.append((override.member.key, row))
    archives = payload.get("member_archives", [])
    if not isinstance(archives, list) or len(archives) > 1000:
        raise ValueError("bounded roster archive inventory required")
    archive_ids = set()
    for archive in archives:
        if (not isinstance(archive, dict) or set(archive) != {"archive_kind", "base_inputs_id", "observation_id", "row", "manual_values", "member_epoch", "overrides", "archive_id"}
                or archive["archive_kind"] != "member_departure"
                or any(not isinstance(archive[key], str) or re.fullmatch("[a-f0-9]{64}", archive[key]) is None for key in ("base_inputs_id", "observation_id"))
                or archive["archive_id"] != _hash({key: value for key, value in archive.items() if key != "archive_id"})):
            raise ValueError("roster archive identity differs")
        if archive["archive_id"] in archive_ids:
            raise ValueError("duplicate roster archive identity")
        archive_ids.add(archive["archive_id"])
        archived = _member_rows([archive["row"]])
        if archive["member_epoch"] is not None:
            validate_member_epochs({next(iter(archived)): archive["member_epoch"]}, (MemberKey(**archive["row"]["member"]),))
        for row in archive["overrides"]:
            override = Override(**{**row, "member": MemberKey(**row["member"])})
            if override.member.key not in archived:
                raise ValueError("archived override member differs")
    added = tuple(MemberKey(**row["member"]) for key, row in observed.items() if key not in current)
    removed = tuple(MemberKey(**row["member"]) for key, row in current.items() if key not in observed) if status == "complete" else ()
    renamed = tuple((MemberKey(**row["member"]), current[key]["display_name"], row["display_name"]) for key, row in observed.items()
        if key in current and current[key]["display_name"] != row["display_name"])
    archived_keys = {MemberKey(**archive["row"]["member"]).key for archive in archives}
    rejoined = tuple(member for member in added if member.key in archived_keys)
    blockers = []
    if current_inputs.envelope["reference_version"] != observation.envelope["reference_version"]:
        blockers.append("roster_reference_transition_required")
    if status != "complete":
        blockers.append("roster_auth_required" if status == "auth_required" else "roster_incomplete")
    dependencies = dict(base_inputs_id=current_inputs.snapshot_id, observation_id=observation.snapshot_id, code_sha=code_sha,
        order_policy=order_policy, added=added, removed=removed, renamed=renamed, rejoined=rejoined)
    if blockers:
        return RosterChangePlan(**dependencies, blockers=tuple(blockers), target_inputs=None, is_noop=False)
    keys = list(observed) if order_policy == "observed" else [key for key in current if key in observed]+[member.key for member in added]
    rows = [{**copy.deepcopy(current.get(key, {})), **observed[key]} for key in keys]
    if not added and not removed and not renamed and rows == list(current.values()):
        return RosterChangePlan(**dependencies, blockers=(), target_inputs=current_inputs, is_noop=True)
    if len(archives)+len(removed) > 1000:
        return RosterChangePlan(**dependencies, blockers=("roster_archive_capacity",), target_inputs=None, is_noop=False)
    for member in removed:
        entry = {"archive_kind": "member_departure", "base_inputs_id": current_inputs.snapshot_id, "observation_id": observation.snapshot_id,
            "row": current[member.key], "manual_values": manual.get(member.key), "member_epoch": epochs.get(member.key),
            "overrides": [row for key, row in overrides if key == member.key]}
        archives.append({**entry, "archive_id": _hash(entry)})
    next_epochs = {key: epoch for key, epoch in epochs.items() if key in observed}
    for member in added:
        next_epochs[member.key] = _hash({"base_inputs_id": current_inputs.snapshot_id, "observation_id": observation.snapshot_id, "member": member.key})
    target = capture_document("union_inputs", current_inputs.envelope["scope_id"], observation.envelope["captured_at"], current_inputs.envelope["reference_version"],
        {**payload, "members": rows, "overrides": [row for key, row in overrides if key in observed],
            "manual_member_values": {key: value for key, value in manual.items() if key in observed}, "member_archives": archives,
            "member_epochs": next_epochs, "roster_state": "active" if rows else "inactive", "roster_observation_id": observation.snapshot_id})
    if len(target.serialized.encode("utf-8")) > 8*1024*1024:
        return RosterChangePlan(**dependencies, blockers=("roster_input_capacity",), target_inputs=None, is_noop=False)
    return RosterChangePlan(**dependencies, blockers=(), target_inputs=target, is_noop=False)


def confirm_roster_change(plan, current_inputs, observation, code_sha, confirmed_plan_id):
    if (plan.plan_id != confirmed_plan_id or plan.code_sha != code_sha or plan.blockers or plan.target_inputs is None
            or prepare_roster_change(current_inputs, observation, code_sha, plan.order_policy) != plan):
        raise ValueError("roster confirmation or source dependency differs")
    return plan.target_inputs


def _member_rows(rows, is_observation=False):
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValueError("bounded explicit roster rows required")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or is_observation and set(row) != {"member", "display_name"}:
            raise ValueError("unknown roster observation columns")
        member = MemberKey(**row["member"])
        RosterMember(member, row["display_name"])
        if member.key in result:
            raise ValueError("duplicate roster member identity")
        result[member.key] = row
    return result


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
