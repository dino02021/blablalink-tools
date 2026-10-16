"""Registered local D1 hold semantics; unknown business schemas fail closed."""

from coordination.d1_checkpoints import SCHEMA_STATEMENTS as CHECKPOINTS
from coordination.d1_compute_queue import SCHEMA_STATEMENTS as COMPUTE
from coordination.d1_migrations import AdditiveMigrationPlan
from coordination.d1_objects import SCHEMA_STATEMENTS as OBJECTS
from coordination.d1_recovery import SCHEMA_STATEMENTS as RECOVERY
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS as HEADS

_REGISTERED = frozenset(entry for statements in (OBJECTS, HEADS, COMPUTE, CHECKPOINTS, RECOVERY)
                        for entry in D1Schema(statements).objects)


def migration_quiescence(plan, revision):
    if (not isinstance(plan, AdditiveMigrationPlan) or type(revision) is not int
            or not 0 <= revision < len(plan.schemas)):
        raise ValueError('fixed migration schema revision required')
    schema = plan.schemas[revision]
    for entry in schema.objects:
        _, name, _, owner = entry
        if entry not in _REGISTERED and not (name.startswith('mock_') and owner.startswith('mock_')):
            raise ValueError('unregistered migration business hold schema')
    tables = {name for kind, name, _, _ in schema.objects if kind == 'table'}
    conditions = []
    if 'compute_jobs' in tables:
        # Expiry does not prove that an actor stopped; recovery is a separate API.
        conditions.append("NOT EXISTS(SELECT 1 FROM compute_jobs WHERE state='running')")
    if 'contract_barriers' in tables:
        conditions.append('NOT EXISTS(SELECT 1 FROM contract_barriers WHERE is_blocked=1)')
    return D1Statement('SELECT CASE WHEN ' + (' AND '.join(conditions) if conditions else '1')
                       + " THEN 1 ELSE json('migration_business_holds') END AS migration_quiescent", ())
