"""Bounded additive schema revisions; SQL contract, not a hosted migration API."""

from dataclasses import dataclass
import hashlib

from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from domain.snapshots import canonical_json

SCHEMA_STATEMENTS = (
    "CREATE TABLE coordinator_schema_authority(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>=0),schema_id TEXT NOT NULL,epoch INTEGER NOT NULL CHECK(epoch>0),state TEXT NOT NULL CHECK(state IN('ready','migrating')),plan_id TEXT,target_revision INTEGER,target_schema_id TEXT,CHECK((state='ready' AND plan_id IS NULL AND target_revision IS NULL AND target_schema_id IS NULL) OR (state='migrating' AND plan_id IS NOT NULL AND target_revision>revision AND target_schema_id IS NOT NULL) OR (state='migrating' AND plan_id IS NOT NULL AND target_revision=revision AND target_schema_id=schema_id))) WITHOUT ROWID",
    'CREATE TABLE coordinator_schema_history(revision INTEGER PRIMARY KEY CHECK(revision>=0),checksum TEXT NOT NULL,before_id TEXT NOT NULL,after_id TEXT NOT NULL,applied_epoch INTEGER NOT NULL CHECK(applied_epoch>0)) WITHOUT ROWID',
    "CREATE TRIGGER coordinator_schema_history_no_update BEFORE UPDATE ON coordinator_schema_history BEGIN SELECT RAISE(ABORT,'immutable_schema_history'); END",
    "CREATE TRIGGER coordinator_schema_history_no_delete BEFORE DELETE ON coordinator_schema_history BEGIN SELECT RAISE(ABORT,'immutable_schema_history'); END",
)
LEDGER = D1Schema(SCHEMA_STATEMENTS)


@dataclass(frozen=True)
class AdditiveMigrationPlan:
    schemas: tuple[D1Schema, ...]

    def __post_init__(self):
        if type(self.schemas) not in (tuple, list) or not 2 <= len(self.schemas) <= 17:
            raise ValueError('one to sixteen bounded additive revisions required')
        object.__setattr__(self, 'schemas', tuple(self.schemas))
        reserved = {item[1] for item in LEDGER.objects}
        for schema in self.schemas:
            if not isinstance(schema, D1Schema) or any(name in reserved or owner in reserved for _, name, _, owner in schema.objects):
                raise ValueError('business schema must not own migration ledger')
        for before, after in zip(self.schemas, self.schemas[1:]):
            if not set(before.objects) < set(after.objects):
                raise ValueError('additive migration must preserve all old declarations and add objects')
        # Reject oversized complete control frames before touching any database.
        bootstrap(self)
        for revision in range(1, len(self.schemas)):
            apply_revision(self, revision, 2)

    @property
    def checksums(self):
        values = [_hash({'protocol': 'd1_additive_v1', 'initial_schema': self.schemas[0].schema_id})]
        for revision in range(1, len(self.schemas)):
            values.append(_hash({'revision': revision, 'previous_checksum': values[-1],
                                 'before_id': self.schemas[revision - 1].schema_id,
                                 'after_id': self.schemas[revision].schema_id,
                                 'statements': self.declarations(revision)}))
        return tuple(values)

    @property
    def plan_id(self):
        return _hash({'protocol': 'd1_additive_v1', 'checksums': self.checksums})

    def declarations(self, revision):
        _revision(self, revision, is_allow_zero=False)
        old = set(self.schemas[revision - 1].objects)
        return tuple(sql for entry in self.schemas[revision].objects if entry not in old for sql in (entry[2],))


def bootstrap(plan):
    initial = plan.schemas[0]
    statements = [_prepared(item) for item in (*LEDGER.bootstrap, *initial.bootstrap)]
    statements.extend((
        D1Statement('INSERT OR IGNORE INTO coordinator_schema_history VALUES(0,?,?,?,1)',
                    (plan.checksums[0], initial.schema_id, initial.schema_id)),
        D1Statement("INSERT OR IGNORE INTO coordinator_schema_authority VALUES(1,0,?,1,'ready',NULL,NULL,NULL)", (initial.schema_id,)),
        history_guard(plan, 0), ready_guard(plan, 0, 1),
    ))
    return _frame(statements)


def begin_migration(plan, base_revision, epoch):
    _revision(plan, base_revision)
    _epoch(epoch)
    if base_revision == len(plan.schemas) - 1 or epoch >= 2 ** 31 - 1:
        raise ValueError('new schema revision and epoch capacity required')
    target = len(plan.schemas) - 1
    return _frame((LEDGER.guard(), plan.schemas[base_revision].guard(), history_guard(plan, base_revision),
        ready_guard(plan, base_revision, epoch),
        D1Statement("UPDATE coordinator_schema_authority SET state='migrating',epoch=?,plan_id=?,target_revision=?,target_schema_id=? WHERE singleton=1",
                    (epoch + 1, plan.plan_id, target, plan.schemas[-1].schema_id)),
        _migration_guard(plan, base_revision, epoch + 1)))


def apply_revision(plan, revision, epoch):
    _revision(plan, revision, is_allow_zero=False)
    _epoch(epoch)
    before, after = plan.schemas[revision - 1:revision + 1]
    return _frame((LEDGER.guard(), _migration_guard(plan, revision - 1, epoch),
        history_guard(plan, revision - 1), before.guard(),
        *(D1Statement(sql, ()) for sql in plan.declarations(revision)), after.guard(),
        D1Statement('INSERT INTO coordinator_schema_history VALUES(?,?,?,?,?)',
                    (revision, plan.checksums[revision], before.schema_id, after.schema_id, epoch)),
        D1Statement('UPDATE coordinator_schema_authority SET revision=?,schema_id=? WHERE singleton=1', (revision, after.schema_id)),
        history_guard(plan, revision), _migration_guard(plan, revision, epoch)))


def finish_migration(plan, epoch):
    _epoch(epoch)
    target = len(plan.schemas) - 1
    # Exact finish replay is a zero-write readback, never a new epoch.
    progress = _migration_guard(plan, target, epoch, is_allow_ready=True)
    return _frame((LEDGER.guard(), progress, history_guard(plan, target), plan.schemas[-1].guard(),
        D1Statement("UPDATE coordinator_schema_authority SET state='ready',plan_id=NULL,target_revision=NULL,target_schema_id=NULL WHERE singleton=1 AND state='migrating'", ()),
        ready_guard(plan, target, epoch)))


def ready_guard(plan, revision, epoch):
    _revision(plan, revision)
    _epoch(epoch)
    condition, parameters = _history_condition(plan, revision)
    return D1Statement("SELECT CASE WHEN EXISTS(SELECT 1 FROM coordinator_schema_authority WHERE singleton=1 AND revision=? AND schema_id=? AND epoch=? AND state='ready' AND plan_id IS NULL AND target_revision IS NULL AND target_schema_id IS NULL) AND (" + condition + ") THEN 1 ELSE json('schema_not_ready') END AS schema_ready",
                       (revision, plan.schemas[revision].schema_id, epoch, *parameters))


def history_guard(plan, revision):
    _revision(plan, revision)
    condition, parameters = _history_condition(plan, revision)
    return D1Statement("SELECT CASE WHEN " + condition + " THEN 1 ELSE json('schema_history_mismatch') END AS history_ok", parameters)


def read_revision(revision):
    if type(revision) is not int or not 0 <= revision <= 16:
        raise ValueError('bounded schema receipt revision required')
    return D1Statement('SELECT revision,checksum,before_id,after_id,applied_epoch FROM coordinator_schema_history WHERE revision=?', (revision,))


def verify_revision_receipt(plan, revision, rows):
    _revision(plan, revision)
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        raise ValueError('positive schema revision receipt required')
    row = rows[0]
    if (set(row) != {'revision', 'checksum', 'before_id', 'after_id', 'applied_epoch'}
            or type(row['revision']) is not int or row['revision'] != revision
            or row['checksum'] != plan.checksums[revision]
            or row['before_id'] != plan.schemas[max(0, revision - 1)].schema_id
            or row['after_id'] != plan.schemas[revision].schema_id):
        raise ValueError('schema revision receipt differs')
    _epoch(row['applied_epoch'])
    return dict(row)


def _migration_guard(plan, revision, epoch, *, is_allow_ready=False):
    target = len(plan.schemas) - 1
    condition = "state='migrating' AND plan_id=? AND target_revision=? AND target_schema_id=?"
    parameters = [revision, plan.schemas[revision].schema_id, epoch, plan.plan_id, target, plan.schemas[-1].schema_id]
    if is_allow_ready:
        condition = '(' + condition + ") OR (state='ready' AND plan_id IS NULL AND target_revision IS NULL AND target_schema_id IS NULL)"
    return D1Statement("SELECT CASE WHEN EXISTS(SELECT 1 FROM coordinator_schema_authority WHERE singleton=1 AND revision=? AND schema_id=? AND epoch=? AND (" + condition + ")) THEN 1 ELSE json('schema_migration_conflict') END AS migration_ok", tuple(parameters))


def _history_condition(plan, revision):
    clauses, parameters = [], []
    checksums = plan.checksums
    for index in range(revision + 1):
        clauses.append('EXISTS(SELECT 1 FROM coordinator_schema_history WHERE revision=? AND checksum=? AND before_id=? AND after_id=?)')
        parameters.extend((index, checksums[index], plan.schemas[max(0, index - 1)].schema_id, plan.schemas[index].schema_id))
    return '(SELECT COUNT(*) FROM coordinator_schema_history)=' + str(revision + 1) + ' AND ' + ' AND '.join(clauses), tuple(parameters)


def _frame(statements):
    statements = tuple(statements)
    if not 1 <= len(statements) <= 50 or len(canonical_json({'kind': 'batch', 'statements': [item.wire for item in statements]}).encode()) > 65536:
        raise ValueError('migration exceeds bounded D1 batch')
    return statements


def _prepared(item):
    return D1Statement(item, ()) if isinstance(item, str) else D1Statement(item['sql'], item['parameters'])


def _revision(plan, value, *, is_allow_zero=True):
    if not isinstance(plan, AdditiveMigrationPlan) or type(value) is not int or not (0 if is_allow_zero else 1) <= value < len(plan.schemas):
        raise ValueError('schema revision is outside the fixed plan')


def _epoch(value):
    if type(value) is not int or not 1 <= value < 2 ** 31:
        raise ValueError('bounded positive schema epoch required')


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
