"""Plan snapshot reuse or bounded supplementary collection; never fetch or enqueue."""

from dataclasses import asdict, dataclass
import hashlib

from application.healthcheck import IncompleteTraining, calculate_healthcheck, healthcheck_input_hash
from domain.execution import check_execution
from domain.member_epochs import validate_member_epochs
from domain.healthcheck import evaluate_metric, parse_score_number
from domain.normalization import normalize_training
from domain.snapshots import MemberKey, apply_overrides, canonical_json, metric_values, read_config, read_training
from domain.standard_layout import METRICS


@dataclass(frozen=True)
class SupplementaryRequest:
    member: MemberKey
    source: str
    role_ids: tuple[str, ...]
    option_ids: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TrainingReusePlan:
    config_id: str
    raw_snapshot_id: str
    reference_hash: str
    function_map_hash: str
    input_hash: str
    member_epoch_hash: str
    code_sha: str
    training_id: str | None
    calculation_id: str | None
    requests: tuple[SupplementaryRequest, ...]
    blockers: tuple[str, ...]
    normalizer_version: int = 1

    @property
    def is_ready(self) -> bool:
        return self.calculation_id is not None and not self.requests and not self.blockers

    @property
    def plan_id(self) -> str:
        return _hash(asdict(self))


def plan_training_reuse(config, raw, reference, function_prefixes, members, display_names, code_sha, overrides=(), member_epochs=None):
    members, overrides = tuple(members), tuple(overrides)
    if (not members or len(members) > 300 or len(reference.role_ids) > 1000
            or any(not isinstance(member, MemberKey) for member in members) or len(set(members)) != len(members)
            or not isinstance(code_sha, str) or not code_sha.strip()):
        raise ValueError("bounded fixed reuse inputs required")
    rules, labels = read_config(config)
    if raw.envelope["kind"] != "raw" or config.envelope["scope_id"] not in ("common", raw.envelope["scope_id"]):
        raise ValueError("reuse source kind or scope differs")
    input_hash = healthcheck_input_hash(members, display_names, overrides)
    required_epochs = validate_member_epochs(member_epochs, members)
    dependencies = dict(config_id=config.snapshot_id, raw_snapshot_id=raw.snapshot_id,
        reference_hash=_hash(asdict(reference)), function_map_hash=_hash(function_prefixes), input_hash=input_hash,
        member_epoch_hash=_hash(required_epochs), code_sha=code_sha)
    if raw.envelope["reference_version"] != reference.version or config.envelope["reference_version"] != reference.version:
        return TrainingReusePlan(**dependencies, training_id=None, calculation_id=None, requests=(), blockers=("reference_reconciliation_required",))
    if raw.payload.get("reference_role_ids") != list(reference.role_ids):
        return TrainingReusePlan(**dependencies, training_id=None, calculation_id=None, requests=(), blockers=("role_coverage_unproven",))
    required_functions = {metric.function_id for rule in rules for metric in rule.metrics if metric.function_id is not None}
    if ({rule.name_code for rule in rules} - set(reference.role_ids) or required_functions - set(reference.function_ids)
            or required_functions - set(function_prefixes.values()) or set(function_prefixes.values()) - set(reference.function_ids)):
        return TrainingReusePlan(**dependencies, training_id=None, calculation_id=None, requests=(), blockers=("reference_mapping_required",))
    roster = {member.key for member in members}
    roles = {rule.name_code for rule in rules}
    if any(override.member.key not in roster or override.name_code not in roles for override in overrides):
        raise ValueError("reuse override is outside pinned inputs")
    normalized = normalize_training(raw, reference.role_ids, function_prefixes)
    raw_members = {MemberKey(member["area_id"], member["member_id"]).key: member for member in raw.payload["members"]}
    blocked_auth = {key for key in roster if key in raw_members and "auth_required" in
        (raw_members[key]["ownership"]["status"], raw_members[key]["details"]["status"])}
    if blocked_auth:
        return TrainingReusePlan(**dependencies, training_id=normalized.training.snapshot_id, calculation_id=None, requests=(), blockers=("auth_required",))
    training_epochs = normalized.training.payload.get("member_epochs", {})
    stale_members = tuple(member for member in members if member.key in required_epochs and training_epochs.get(member.key) != required_epochs[member.key])
    if stale_members:
        requests = tuple(SupplementaryRequest(member, "ownership", tuple(sorted(rule.name_code for rule in rules)), (), ("membership_epoch_unproven",)) for member in stale_members)
        return TrainingReusePlan(**dependencies, training_id=normalized.training.snapshot_id, calculation_id=None, requests=requests, blockers=())
    try:
        result = calculate_healthcheck(config, normalized.training, members, display_names, code_sha, overrides)
        return TrainingReusePlan(**dependencies, training_id=normalized.training.snapshot_id, calculation_id=result.result_id, requests=(), blockers=())
    except IncompleteTraining:
        pass
    indexed = {record.key: record for record in apply_overrides(read_training(normalized.training), overrides)}
    priorities = {label.name_code: label.priority for label in labels}
    pending, blockers = {}, set()
    for member in members:
        check_execution()
        source = raw_members.get(member.key)
        for rule in rules:
            check_execution()
            if not rule.is_score_tracked and priorities.get(rule.name_code) == "忽略":
                continue
            record = indexed.get((member.key, rule.name_code))
            if record is None or record.ownership_status == "unknown":
                _require(pending, member, "ownership", rule.name_code, "ownership_unknown")
                continue
            if record.ownership_status == "not_owned":
                continue
            values = metric_values(rule, record)
            for metric in rule.metrics:
                score_required = rule.is_score_tracked and parse_score_number(metric.coefficient) not in (None, 0)
                report_unknown = priorities.get(rule.name_code) != "忽略" and evaluate_metric(metric, values[metric.metric_id]).status == "unknown"
                if not report_unknown and not (score_required and parse_score_number(values[metric.metric_id]) is None):
                    continue
                if metric.metric_id == "breakthrough":
                    _require(pending, member, "ownership", rule.name_code, "breakthrough_unknown")
                elif metric.function_id is not None:
                    _require_traits(pending, member, rule.name_code, metric.function_id, source, function_prefixes)
                elif metric.metric_id in METRICS:
                    _require(pending, member, "details", rule.name_code, "metric_unknown")
                else:
                    blockers.add("unsupported_metric_source")
    requests = tuple(SupplementaryRequest(member, source, tuple(sorted(data["roles"])), tuple(sorted(data["options"])), tuple(sorted(data["reasons"])))
        for (member, source), data in sorted(pending.items(), key=lambda item: (item[0][0].key, item[0][1])))
    if not requests and not blockers:
        blockers.add("unclassified_training_gap")
    return TrainingReusePlan(**dependencies, training_id=normalized.training.snapshot_id, calculation_id=None,
        requests=requests, blockers=tuple(sorted(blockers)))


def _require(pending, member, source, role, reason, option_ids=()):
    request = pending.setdefault((member, source), {"roles": set(), "options": set(), "reasons": set()})
    request["roles"].add(role)
    request["options"].update(option_ids)
    request["reasons"].add(reason)


def _require_traits(pending, member, role, function_id, source, prefixes):
    details = source["details"] if source is not None else None
    row = next((row for row in details["character_details"] if row["name_code"] == role), None) if details else None
    if row is None or details["status"] != "complete" or not details["is_options_complete"]:
        _require(pending, member, "details", role, "option_coverage_unknown")
        _require(pending, member, "effects", role, "option_coverage_unknown")
        return
    options = tuple(sorted({str(value) for key, value in row.items() if key.startswith("equip_option")
        and value not in (None, "", 0, "0") and prefixes.get(str(value)[:5]) == function_id}))
    _require(pending, member, "effects", role, "state_effect_unknown", options)


def _hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
