"""Conservative local object GC with explicit immutable plans and reference closure."""

from dataclasses import asdict, dataclass
import hashlib
import json
import math

from adapters.local_state import LocalStateStore, _transaction
from application.tasks import StateConflict
from domain.snapshots import canonical_json


@dataclass(frozen=True)
class ObjectRetentionPlan:
    cutoff: float
    limit: int
    inventory_hash: str
    object_ids: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.cutoff) not in (int, float) or not math.isfinite(self.cutoff)
                or type(self.limit) is not int or not 1 <= self.limit <= 1000
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError("invalid bounded object retention plan")
        object.__setattr__(self, "object_ids", tuple(self.object_ids))
        if len(set(self.object_ids)) != len(self.object_ids) or len(self.object_ids) > self.limit:
            raise ValueError("invalid object deletion set")
        for digest in (self.inventory_hash, *self.object_ids):
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid retention digest")

    @property
    def plan_id(self):
        return _hash(canonical_json(asdict(self)))


class LocalObjectRetention(LocalStateStore):
    def plan_retention(self, cutoff: float, limit: int = 100) -> ObjectRetentionPlan:
        ObjectRetentionPlan(cutoff, limit, "0" * 64, ())  # validate before any scan
        with _transaction(self._connection):
            return self._plan(cutoff, limit)

    def apply_retention(self, plan: ObjectRetentionPlan, confirmed_plan_id: str) -> tuple[str, ...]:
        if plan.plan_id != confirmed_plan_id:
            raise StateConflict("retention confirmation differs")
        with _transaction(self._connection):
            receipt = self._connection.execute("SELECT object_ids_json FROM object_retention_receipts WHERE plan_id=?", (plan.plan_id,)).fetchone()
            if receipt:
                if canonical_json(plan.object_ids) != receipt[0]:
                    raise StateConflict("retention receipt corrupted")
                return plan.object_ids
            current = self._plan(plan.cutoff, plan.limit)
            if current != plan:
                raise StateConflict("object inventory or retained references changed")
            self._connection.executemany("DELETE FROM objects WHERE object_id=?", ((object_id,) for object_id in plan.object_ids))
            self._connection.execute("INSERT INTO object_retention_receipts VALUES(?,?,?)",
                (plan.plan_id, canonical_json(plan.object_ids), self._now()))
            return plan.object_ids

    def _plan(self, cutoff, limit):
        objects = {}
        size = 0
        for row in self._connection.execute("SELECT object_id,serialized,created_at FROM objects ORDER BY object_id"):
            size += len(row["serialized"].encode())
            if len(objects) >= 10000 or size > 64 * 1024 * 1024:
                raise ValueError("retention inventory exceeds bounded local scan")
            if _hash(row["serialized"]) != row["object_id"]:
                raise StateConflict("object inventory corrupted")
            objects[row["object_id"]] = (row["serialized"], row["created_at"])
        identities = set(objects)
        roots, scanned_rows, root_bytes = set(), 0, 0
        tables = [row[0] for row in self._connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            if table in ("objects", "object_retention_receipts"):
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            for row in self._connection.execute("SELECT * FROM " + quoted):
                scanned_rows += 1
                if scanned_rows > 100000:
                    raise ValueError("retention root scan exceeds local bound")
                for value in row:
                    if isinstance(value, str):
                        root_bytes += len(value.encode())
                        if root_bytes > 64 * 1024 * 1024:
                            raise ValueError("retention root bytes exceed local bound")
                        roots.update(_references(value, identities))
        reachable = set(roots)
        pending = list(roots)
        while pending:
            object_id = pending.pop()
            for reference in _references(objects[object_id][0], identities) - reachable:
                reachable.add(reference)
                pending.append(reference)
        candidates = tuple(object_id for object_id, (_, created) in sorted(objects.items(), key=lambda item: (item[1][1], item[0]))
                           if created < cutoff and object_id not in reachable)[:limit]
        inventory_hash = _hash(canonical_json({"objects": [(key, value[1]) for key, value in objects.items()],
            "roots": sorted(roots), "reachable": sorted(reachable)}))
        return ObjectRetentionPlan(cutoff, limit, inventory_hash, candidates)


def _references(text, identities):
    references = {text} & identities
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return references
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if value in identities:
                references.add(value)
        elif isinstance(value, dict):
            pending.extend(value.values())
            pending.extend(value.keys())
        elif isinstance(value, list):
            pending.extend(value)
    return references


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()
