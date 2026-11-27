"""Real PostgreSQL tests. Run only against the dedicated disposable quota_test DB.

CHATBOT_QUOTA_TEST_ISOLATED=1 LLM_QUOTA_TEST_DSN='...' python -m pytest -q tests/test_llm_quota_store_pg.py
"""
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool
import pytest

from rag_core.llm_quota import LIMITS, QuotaExceeded
from rag_core.llm_quota_store import Amount, PostgresQuotaStore, ReservationConflict, ReservationStateError

DAY = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)
ZERO = dict.fromkeys(LIMITS, 0)
DDL = Path(__file__).resolve().parents[1] / 'scripts/sql/llm_quota_ledger.sql'


@pytest.fixture(scope='session')
def target():
    if os.environ.get('CHATBOT_QUOTA_TEST_ISOLATED') != '1' or not os.environ.get('LLM_QUOTA_TEST_DSN'):
        pytest.fail('CHATBOT_QUOTA_TEST_ISOLATED=1 and LLM_QUOTA_TEST_DSN are required', pytrace=False)
    # libpq environment variables can redirect connections or inject session options.
    if any(key.startswith('PG') for key in os.environ):
        pytest.fail('PG* environment variables are forbidden for isolated tests', pytrace=False)
    try:
        parts = conninfo_to_dict(os.environ['LLM_QUOTA_TEST_DSN'])
    except Exception:
        pytest.fail('invalid test DSN', pytrace=False)
    allowed = {'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'connect_timeout'}
    if (set(parts) - allowed or parts.get('host') != '127.0.0.1'
            or parts.get('dbname') != 'quota_test' or parts.get('user') != 'quota_test'):
        pytest.fail('test DSN must explicitly target 127.0.0.1/quota_test as quota_test', pytrace=False)
    parts.update(hostaddr='127.0.0.1', connect_timeout='5')
    return parts


def connect(target):
    try:
        conn = psycopg.connect(**target, autocommit=True)
    except psycopg.Error:
        pytest.fail('isolated PostgreSQL connection failed', pytrace=False)
    try:
        if conn.execute('SELECT current_database(), current_user').fetchone() != ('quota_test', 'quota_test'):
            raise RuntimeError('connected database or user is not quota_test')
        conn.execute("SET lock_timeout = '3s'")
        conn.execute("SET statement_timeout = '8s'")
        return conn
    except BaseException:
        conn.close()
        raise


@pytest.fixture
def database(target):
    schema = 'quota_test_' + uuid4().hex
    admin = connect(target)
    created = False
    try:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        created = True
        admin.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        admin.execute(DDL.read_text())

        def configure(conn):
            if conn.execute('SELECT current_database(), current_user').fetchone() != ('quota_test', 'quota_test'):
                raise RuntimeError('wrong database or user')
            conn.execute("SET lock_timeout = '3s'")
            conn.execute("SET statement_timeout = '8s'")
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            conn.autocommit = False

        @contextmanager
        def factory():
            conn = connect(target)
            try:
                conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
                conn.autocommit = False
                yield conn
            finally:
                conn.close()

        yield admin, factory, configure
    finally:
        if created:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        admin.close()


def reserve(store, rid='id', amount=Amount(1, 1, '1'), limits=ZERO, tenant='tenant', caller='caller'):
    return store.reserve(rid, DAY, tenant, caller, 'chat', 'default', amount, limits)


def test_schema_contract(database):
    conn, _, _ = database
    rows = conn.execute("""SELECT table_name, column_name, data_type FROM information_schema.columns
        WHERE table_schema = current_schema() ORDER BY table_name, ordinal_position""").fetchall()
    columns = {(table, col): typ for table, col, typ in rows}
    assert columns['llm_quota_ledger_days', 'day_start'] == 'timestamp with time zone'
    for table in ('llm_quota_ledger_days', 'llm_quota_ledger_reservations'):
        for name, typ in [('reserved_calls', 'bigint'), ('reserved_tokens', 'bigint'), ('reserved_cost', 'numeric'),
                          ('settled_calls', 'bigint'), ('settled_tokens', 'bigint'), ('settled_cost', 'numeric')]:
            assert columns[table, name] == typ
    for name in ('reservation_id', 'tenant_id', 'caller_fingerprint', 'feature', 'profile', 'status'):
        assert columns['llm_quota_ledger_reservations', name] == 'text'
    constraints = conn.execute("""SELECT contype, count(*) FROM pg_constraint
        WHERE connamespace = current_schema()::regnamespace GROUP BY contype""").fetchall()
    counts = dict(constraints)
    assert counts['p'] == 2 and counts['f'] == 1 and counts['c'] >= 15
    indexes = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()").fetchall()
    assert {r[0] for r in indexes} >= {'llm_quota_ledger_days_pkey', 'llm_quota_ledger_reservations_pkey', 'llm_quota_ledger_tenant_day_idx'}
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute('INSERT INTO llm_quota_ledger_days(day_start, reserved_calls) VALUES (%s, -1)', (DAY,))


@pytest.mark.parametrize('limit', ['user_daily_calls', 'tenant_daily_tokens', 'tenant_daily_cost', 'global_daily_cost'])
def test_simultaneous_reservations(database, limit):
    conn, factory, _ = database
    barrier = Barrier(2, timeout=5)
    def worker(i):
        barrier.wait()
        try:
            return reserve(PostgresQuotaStore(factory), str(i), limits={**ZERO, limit: 1})
        except QuotaExceeded:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, range(2)))
    assert sorted(results) == [False, True]
    assert conn.execute('SELECT count(*) FROM llm_quota_ledger_days').fetchone()[0] == 1
    assert conn.execute('SELECT count(*) FROM llm_quota_ledger_reservations').fetchone()[0] == 1


@pytest.mark.parametrize('different', [False, True])
def test_simultaneous_id_collision(database, different):
    _, factory, _ = database
    barrier = Barrier(2, timeout=5)
    def worker(i):
        barrier.wait()
        try:
            return reserve(PostgresQuotaStore(factory), amount=Amount(1, 1, str(i + 1) if different else '1'))
        except ReservationConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, range(2)))
    assert results.count(True) == 1
    assert ('conflict' if different else False) in results


def test_numeric_roundtrip_and_limits(database):
    conn, factory, _ = database
    store = PostgresQuotaStore(factory)
    tiny = Decimal('1e-1000')
    with localcontext() as ctx:
        ctx.prec = 5
        assert reserve(store, 'tiny', Amount(0, 0, tiny), {**ZERO, 'global_daily_cost': tiny})
        with pytest.raises(QuotaExceeded):
            reserve(store, 'exceed', Amount(0, 0, tiny), {**ZERO, 'global_daily_cost': tiny})
    assert conn.execute("SELECT reserved_cost FROM llm_quota_ledger_reservations WHERE reservation_id='tiny'").fetchone()[0] == tiny
    assert store.usage(DAY).cost == tiny
    # NUMERIC supports at most 16383 fractional digits and 131072 before the point.
    assert reserve(store, 'scale', Amount(0, 0, Decimal('1e-16383')))
    assert conn.execute("SELECT reserved_cost FROM llm_quota_ledger_reservations WHERE reservation_id='scale'").fetchone()[0] == Decimal('1e-16383')
    with pytest.raises((psycopg.Error, ValueError, OverflowError)):
        reserve(store, 'too-large', Amount(0, 0, Decimal('1e131072')))
    assert conn.execute("SELECT count(*) FROM llm_quota_ledger_reservations WHERE reservation_id='too-large'").fetchone()[0] == 0


def test_settlement_and_idempotence(database):
    conn, factory, _ = database
    store = PostgresQuotaStore(factory)
    assert reserve(store) is True
    assert reserve(store) is False
    with pytest.raises(ReservationConflict):
        reserve(store, tenant='different')
    # A pending/unknown result remains reserved until explicitly settled.
    assert store.usage(DAY) == Amount(1, 1, '1')
    store.settle('id', Amount(2, 3, '2.5'))
    with pytest.raises(ReservationStateError):
        store.settle('id', Amount(2, 3, '2.5'))
    assert store.usage(DAY) == Amount(2, 3, '2.5')
    assert conn.execute('SELECT reserved_calls, settled_calls, reserved_cost, settled_cost FROM llm_quota_ledger_days').fetchone() == (0, 2, Decimal(0), Decimal('2.5'))


def test_rollback_after_partial_update_and_disconnect(database):
    conn, factory, _ = database
    # Trigger a database error after insertion, before the aggregate update.
    conn.execute("""CREATE FUNCTION fail_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'injected failure'; END $$""")
    conn.execute('CREATE TRIGGER fail_update BEFORE UPDATE ON llm_quota_ledger_days FOR EACH ROW EXECUTE FUNCTION fail_update()')
    with pytest.raises(psycopg.Error):
        reserve(PostgresQuotaStore(factory))
    assert conn.execute('SELECT count(*) FROM llm_quota_ledger_reservations').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM llm_quota_ledger_days').fetchone()[0] == 0
    conn.execute('DROP TRIGGER fail_update ON llm_quota_ledger_days')
    # Closing an uncommitted connection has a known rollback outcome; a lost commit reply does not.
    with factory() as other:
        other.execute('INSERT INTO llm_quota_ledger_days(day_start) VALUES (%s)', (DAY,))
    assert conn.execute('SELECT count(*) FROM llm_quota_ledger_days').fetchone()[0] == 0


def test_transaction_contract(database):
    _, factory, _ = database
    with factory() as conn:
        @contextmanager
        def same():
            yield conn
        store = PostgresQuotaStore(same)
        assert reserve(store)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        with conn.transaction():
            conn.execute('SELECT 1')
            with pytest.raises(RuntimeError, match='idle connection'):
                store.usage(DAY)
            with conn.transaction():  # savepoint
                with pytest.raises(RuntimeError, match='idle connection'):
                    store.usage(DAY)
        conn.execute('SELECT 1')  # implicit transaction
        with pytest.raises(RuntimeError, match='idle connection'):
            store.usage(DAY)
        conn.rollback()
        assert store.usage(DAY) == Amount(1, 1, '1')


def test_pool_reuse(database, target):
    _, _, configure = database
    # pool configure runs on autocommit connections and leaves each connection idle.
    with ConnectionPool(conninfo='', kwargs={**target, 'autocommit': True}, min_size=1, max_size=1,
                        timeout=5, configure=configure) as pool:
        with pool.connection() as conn:
            first = conn.info.backend_pid
        store = PostgresQuotaStore(pool.connection)
        assert reserve(store)
        assert store.usage(DAY) == Amount(1, 1, '1')
        with pool.connection() as conn:
            assert conn.info.backend_pid == first
            assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_fractional_scale_overflow_is_not_silent(database):
    conn, factory, _ = database
    value = Decimal('1e-16384')
    try:
        reserve(PostgresQuotaStore(factory), 'beyond-scale', Amount(0, 0, value))
    except (psycopg.Error, ValueError, OverflowError):
        assert conn.execute("SELECT count(*) FROM llm_quota_ledger_reservations WHERE reservation_id='beyond-scale'").fetchone()[0] == 0
    else:
        stored = conn.execute("SELECT reserved_cost FROM llm_quota_ledger_reservations WHERE reservation_id='beyond-scale'").fetchone()[0]
        assert stored == value, 'PostgreSQL silently rounded a reservation cost'


@pytest.fixture
def conversation_database(database, monkeypatch):
    from webapi import conversation_store as cs

    admin, factory, _ = database

    @contextmanager
    def transaction():
        with factory() as conn:
            with conn.transaction():
                yield conn

    monkeypatch.setattr(cs, "_pg_conn", transaction)
    monkeypatch.setenv("CONVERSATION_STORE_BACKEND", "postgres")
    monkeypatch.setenv("CONVERSATION_RETENTION_DAYS", "0")
    return cs, admin, factory


def test_conversation_v2_postgres_crud_and_legacy_isolation(conversation_database):
    cs, admin, _ = conversation_database
    cs.append_turn("t", "u", "thread", question="first", answer_text="A", source_docs=["x"])
    admin.execute("INSERT INTO conversation_turns (tenant_id, identity, thread_id, ts, record)"
                  " VALUES ('t','u','thread',1,'{}'::jsonb)")
    for tenant, identity, thread in [("_t", "u", "thread"), ("t", "_u", "thread"), ("t", "u", "_thread")]:
        assert cs.get_thread(tenant, identity, thread) == []
        cs.append_turn(tenant, identity, thread, question="other", answer_text="B", source_docs=["x"])
    assert {row["thread_id"] for row in cs.list_threads("t", "u")} == {"thread", "_thread"}
    assert cs.redact_source_doc("t", "x") == 3
    assert cs.redact_source_doc("t", "x") == 0
    assert cs.get_thread("_t", "u", "thread")[0]["answer_text"] == "B"
    assert cs.get_thread("t", "_u", "thread")[0]["answer_text"] == cs.REDACTED_NOTICE
    assert cs.delete_thread("t", "u", "thread")
    assert cs.get_thread("t", "u", "thread") == []
    assert cs.purge("t", "u", max_threads=0) == 1
    assert admin.execute("SELECT COUNT(*) FROM conversation_turns WHERE tenant_id = 't'").fetchone()[0] == 1


def test_conversation_postgres_marker_survives_rewrite_failure(conversation_database):
    cs, admin, _ = conversation_database
    cs.append_turn("t", "u", "th", question="q", answer_text="private", source_docs=["x"])
    admin.execute("""CREATE FUNCTION fail_conversation_rewrite() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'injected failure'; END $$""")
    admin.execute("CREATE TRIGGER fail_rewrite BEFORE UPDATE ON conversation_turns"
                  " FOR EACH ROW EXECUTE FUNCTION fail_conversation_rewrite()")
    with pytest.raises(psycopg.Error):
        cs.redact_source_doc("t", "x")
    assert admin.execute("SELECT count(*) FROM conversation_redactions_v2").fetchone()[0] == 1
    assert cs.get_thread("t", "u", "th")[0]["answer_text"] == cs.REDACTED_NOTICE
    assert cs.append_turn("t", "u", "th", question="late", answer_text="private", source_docs=["x"])["redacted"]
    admin.execute("DROP TRIGGER fail_rewrite ON conversation_turns")
    assert cs.redact_source_doc("t", "x") == 1
    assert cs.redact_source_doc("t", "x") == 0


def test_conversation_postgres_schema_rollback_retries(conversation_database):
    cs, admin, factory = conversation_database
    with pytest.raises(RuntimeError, match="rollback"):
        with factory() as conn:
            with conn.transaction():
                cs._pg_ensure(conn)
                raise RuntimeError("rollback")
    assert admin.execute("SELECT to_regclass('conversation_redactions_v2')").fetchone()[0] is None
    cs.append_turn("t", "u", "th", question="q")
    assert cs.get_thread("t", "u", "th")[0]["question"] == "q"


def test_conversation_postgres_append_retention_rollback(conversation_database, monkeypatch):
    cs, _, _ = conversation_database
    cs.append_turn("t", "u", "th", question="old")
    with monkeypatch.context() as patch:
        def fail_purge(*args, **kwargs):
            raise RuntimeError("retention failed")
        patch.setattr(cs, "_pg_purge_locked", fail_purge)
        with pytest.raises(RuntimeError, match="retention failed"):
            cs.append_turn("t", "u", "th", question="new")
    assert [row["question"] for row in cs.get_thread("t", "u", "th")] == ["old"]


def test_conversation_postgres_concurrent_append_redaction(conversation_database):
    cs, _, _ = conversation_database
    cs.append_turn("t", "u", "th", question="old", answer_text="private", source_docs=["x"])
    barrier = Barrier(2, timeout=5)

    def append():
        barrier.wait()
        return cs.append_turn("t", "u", "th", question="late", answer_text="private", source_docs=["x"])

    def redact():
        barrier.wait()
        return cs.redact_source_doc("t", "x")

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(append)
        remover = executor.submit(redact)
        writer.result(timeout=15)
        remover.result(timeout=15)
    turns = cs.get_thread("t", "u", "th")
    assert {row["question"] for row in turns} == {"old", "late"}
    assert all(row["redacted"] and row["answer_text"] == cs.REDACTED_NOTICE for row in turns)


def test_conversation_postgres_rejects_invalid_provenance_before_write(conversation_database):
    cs, admin, _ = conversation_database
    for kwargs in ({"citations_count": 1}, {"source_docs": [""]}, {"source_docs": [1]},
                   {"source_docs": ["x" * 513]}, {"source_docs": [str(i) for i in range(21)]}):
        with pytest.raises(ValueError):
            cs.append_turn("t", "u", "th", question="q", **kwargs)
    assert admin.execute("SELECT to_regclass('conversation_turns')").fetchone()[0] is None
    rec = cs.append_turn("t", "u", "th", question="q", citations_count=2, source_docs=[" a ", "a", "b"])
    assert rec["source_docs"] == ["a", "b"]
    assert cs.get_thread("t", "u", "th")[0]["source_docs"] == ["a", "b"]
