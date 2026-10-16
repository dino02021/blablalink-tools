import sqlite3

import pytest

from coordination.d1_compute_queue import SCHEMA_STATEMENTS as COMPUTE
from coordination.d1_migration_policy import migration_quiescence
from coordination.d1_migrations import AdditiveMigrationPlan
from coordination.d1_objects import SCHEMA_STATEMENTS as OBJECTS
from coordination.d1_schema import D1Schema
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS as HEADS


def plan_for(statements):
    initial = D1Schema(statements)
    return AdditiveMigrationPlan((initial, D1Schema((*initial.statements, 'CREATE TABLE mock_future(id TEXT)'))))


@pytest.mark.parametrize('executor_type', ['local', 'actions', 'vercel'])
def test_registered_quiescence_requires_no_live_compute_or_blocked_scope(executor_type):
    plan = plan_for((*OBJECTS, *HEADS, *COMPUTE))
    guard = migration_quiescence(plan, 0)
    with sqlite3.connect(':memory:') as connection:
        for statement in plan.schemas[0].statements:
            connection.execute(statement)
        assert connection.execute(guard.sql).fetchone() == (1,)
        connection.execute("INSERT INTO contract_barriers VALUES('mock_union',1)")
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(guard.sql)
        connection.execute('UPDATE contract_barriers SET is_blocked=0')
        connection.execute("INSERT INTO compute_jobs(job_id,spec_id,input_signature,command,scope_id,family,code_sha,executor_type,priority,max_attempts,state,owner,expires_at_ms,next_attempt_at_ms,created_at_ms,updated_at_ms) VALUES('mock-job','mock-spec','mock-input','refresh_raid_history','mock_union','raid','mock-release',?,0,3,'running','old-worker',1,0,0,0)", (executor_type,))
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(guard.sql)
        # Only explicit recovery/transition outside migration can release it.
        connection.execute("UPDATE compute_jobs SET state='retry_wait',owner=NULL,expires_at_ms=NULL")
        assert connection.execute(guard.sql).fetchone() == (1,)


@pytest.mark.parametrize('state', ['queued', 'retry_wait', 'computed', 'succeeded', 'superseded', 'failed'])
def test_historical_or_waiting_jobs_are_not_fabricated_active_holds(state):
    plan = plan_for(COMPUTE)
    with sqlite3.connect(':memory:') as connection:
        for statement in COMPUTE:
            connection.execute(statement)
        connection.execute("INSERT INTO compute_jobs(job_id,spec_id,input_signature,command,scope_id,family,code_sha,executor_type,priority,max_attempts,state,next_attempt_at_ms,created_at_ms,updated_at_ms) VALUES('mock-job','mock-spec','mock-input','refresh_raid_history','mock_union','raid','mock-release','local',0,3,?,0,0,0)", (state,))
        assert connection.execute(migration_quiescence(plan, 0).sql).fetchone() == (1,)


@pytest.mark.parametrize('statements', [('CREATE TABLE resource_locks(id TEXT)',),
    ('CREATE TABLE publisher_holds(id TEXT)',), ('CREATE TABLE outbox_unknown(id TEXT)',),
    ('CREATE TABLE compute_jobs(id TEXT)',),
    (*COMPUTE, 'CREATE TRIGGER mock_redirect AFTER INSERT ON compute_jobs BEGIN SELECT 1; END')])
def test_unknown_business_holds_need_registered_policy(statements):
    plan = plan_for(statements)
    with pytest.raises(ValueError, match='unregistered'):
        migration_quiescence(plan, 0)


def test_fixture_namespace_is_explicit_and_invalid_revision_is_not_an_idle_claim():
    plan = plan_for(('CREATE TABLE mock_fixture(id TEXT)',))
    assert migration_quiescence(plan, 0).parameters == ()
    for revision in (True, -1, 2, '0'):
        with pytest.raises(ValueError):
            migration_quiescence(plan, revision)
