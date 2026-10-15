"""Immutable report delivery intent. No transport or credential imports."""

from dataclasses import asdict, dataclass
import hashlib

from domain.snapshots import canonical_json


@dataclass(frozen=True)
class DeliverySpec:
    intent_id: str
    report_version: str
    member_id: str
    route_version: str
    target_thread: str
    parts: tuple[str, ...]
    mention_user_ids: tuple[str, ...] = ()

    def __post_init__(self):
        for value in (self.intent_id, self.report_version, self.member_id, self.route_version, self.target_thread):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("fixed delivery identity and versions required")
        if isinstance(self.parts, str) or isinstance(self.mention_user_ids, str):
            raise ValueError("delivery sequences required")
        parts, users = tuple(self.parts), tuple(self.mention_user_ids)
        if not parts or any(not isinstance(p, str) or not p or len(p.encode('utf-16-le')) // 2 > 2000 for p in parts):
            raise ValueError("each message must contain 1..2000 UTF-16 units")
        if len(set(users)) != len(users) or any(not isinstance(u, str) or not u.isascii() or not u.isdigit() for u in users):
            raise ValueError("explicit unique user mention IDs required")
        object.__setattr__(self, "parts", parts)
        object.__setattr__(self, "mention_user_ids", users)

    @property
    def serialized(self) -> str:
        return canonical_json(asdict(self))

    @property
    def outbox_id(self) -> str:
        return hashlib.sha256(self.serialized.encode()).hexdigest()

    def payload(self, ordinal: int) -> dict:
        if type(ordinal) is not int or not 0 <= ordinal < len(self.parts):
            raise ValueError("invalid delivery part")
        return {"content": self.parts[ordinal], "allowed_mentions": {
            "parse": [], "users": list(self.mention_user_ids) if ordinal == 0 else [],
            "roles": [], "replied_user": False}}


@dataclass(frozen=True)
class DeliveryClaim:
    outbox_id: str
    ordinal: int
    worker_id: str
    generation: int


class NotSent(RuntimeError):
    """Provider positively rejected before creating a message (e.g. known rate limit)."""

    def __init__(self, retry_after: float = 0):
        super().__init__("provider confirmed not sent")
        self.retry_after = retry_after


def send_next(outbox, provider, outbox_id: str, worker_id: str) -> bool:
    claim = outbox.claim(outbox_id, worker_id)
    if claim is None:
        return False
    spec = outbox.spec(outbox_id)
    try:
        before_send = getattr(outbox, "before_send", None)
        if before_send is not None and before_send(claim) is not True:
            return False
        message_id = provider.send(spec.target_thread, spec.payload(claim.ordinal))
    except NotSent as error:
        outbox.reject(claim, error.retry_after)
        return False
    except (TimeoutError, OSError):
        outbox.uncertain(claim)
        return False
    outbox.sent(claim, message_id)
    return True
