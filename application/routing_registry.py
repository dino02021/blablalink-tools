"""Local routing administration contracts, distinct from remote identity authentication."""

from dataclasses import asdict, dataclass
import hashlib

from application.tasks import validate_identifier
from domain.snapshots import MemberKey, canonical_json


@dataclass(frozen=True)
class RoutingAdministrator:
    actor_id: str
    scope_ids: tuple[str, ...]

    def __post_init__(self):
        validate_identifier(self.actor_id)
        if isinstance(self.scope_ids, str):
            raise ValueError("explicit administrator scopes required")
        scopes = tuple(self.scope_ids)
        if not scopes or len(scopes) > 100 or len(set(scopes)) != len(scopes):
            raise ValueError("bounded unique administrator scopes required")
        for scope in scopes:
            validate_identifier(scope)
        object.__setattr__(self, "scope_ids", scopes)


@dataclass(frozen=True)
class SenderGrant:
    scope_id: str
    principal_id: str
    allowed_threads: tuple[str, ...]
    allowed_mentions: tuple[str, ...] = ()
    is_enabled: bool = True

    def __post_init__(self):
        validate_identifier(self.scope_id)
        validate_identifier(self.principal_id)
        if isinstance(self.allowed_threads, str) or isinstance(self.allowed_mentions, str) or type(self.is_enabled) is not bool:
            raise ValueError("explicit sender grant required")
        threads, mentions = tuple(self.allowed_threads), tuple(self.allowed_mentions)
        if (len(set(threads)) != len(threads) or len(threads) > 1000 or self.is_enabled and not threads
                or len(set(mentions)) != len(mentions) or len(mentions) > 1000):
            raise ValueError("bounded unique grant allowlists required")
        for thread in threads:
            validate_identifier(thread)
        if any(not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 32 for value in mentions):
            raise ValueError("explicit decimal mention allowlist required")
        object.__setattr__(self, "allowed_threads", tuple(sorted(threads)))
        object.__setattr__(self, "allowed_mentions", tuple(sorted(mentions)))

    @property
    def grant_id(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


def validate_route_snapshot(document) -> None:
    payload = document.payload
    if (document.envelope["kind"] != "union_inputs" or set(payload) != {"routing_kind", "routes"}
            or payload["routing_kind"] != "report_routes" or not isinstance(payload["routes"], list)
            or not 1 <= len(payload["routes"]) <= 1000 or len(document.serialized.encode()) > 2*1024*1024):
        raise ValueError("bounded explicit report route snapshot required")
    seen = set()
    for row in payload["routes"]:
        if not isinstance(row, dict) or set(row) - {"member", "target_thread", "mention_user_id"}:
            raise ValueError("unknown route columns")
        member = MemberKey(**row["member"])
        if member.key in seen:
            raise ValueError("duplicate route member identity")
        seen.add(member.key)
        validate_identifier(row["target_thread"])
        mention = row.get("mention_user_id")
        if mention is not None and (not isinstance(mention, str) or not mention.isascii() or not mention.isdigit() or not 1 <= len(mention) <= 32):
            raise ValueError("invalid route mention identity")
