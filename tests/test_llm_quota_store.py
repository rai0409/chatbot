from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from rag_core.llm_quota import LIMITS, QuotaExceeded
from rag_core.llm_quota_store import Amount, PostgresQuotaStore, ReservationConflict, ReservationStateError

DAY = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)
ZERO = {key: 0 for key in LIMITS}


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.days = {}
        self.statements = []
        self.fail_on = None
        self.events = []

    @contextmanager
    def factory(self):
        yield self

    @contextmanager
    def transaction(self):
        before = deepcopy((self.rows, self.days))
        self.events.append('begin')
        try:
            yield
        except BaseException:
            self.rows, self.days = before
            self.events.append('rollback')
            raise
        else:
            self.events.append('commit')

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError('database failure')
        if 'SET TRANSACTION ISOLATION LEVEL' in sql:
            return Cursor()
        if 'pg_advisory_xact_lock' in sql:
            return Cursor()
        if 'INSERT INTO llm_quota_ledger_days' in sql:
            self.days.setdefault(params[0], [0, 0, Decimal(0), 0, 0, Decimal(0)])
        elif 'SELECT day_start FROM llm_quota_ledger_days' in sql:
            return Cursor((params[0],))
        elif 'SELECT day_start, tenant_id' in sql:
            row = self.rows.get(params[0])
            return Cursor(tuple(row[:8]) if row else None)
        elif 'SELECT day_start, reserved_calls' in sql:
            row = self.rows.get(params[0])
            return Cursor((row[0], *row[5:8], row[8]) if row else None)
        elif 'COALESCE(SUM' in sql:
            day, tenant, _, caller, _ = params
            selected = [r for r in self.rows.values() if r[0] == day and (tenant is None or r[1] == tenant)
                        and (caller is None or r[2] == caller)]
            amounts = [r[5:8] if r[8] == 'reserved' else r[9:12] for r in selected]
            return Cursor(tuple(sum((a[i] for a in amounts), Decimal(0) if i == 2 else 0) for i in range(3)))
        elif 'INSERT INTO llm_quota_ledger_reservations' in sql:
            self.rows[params[0]] = list(params[1:]) + ['reserved', None, None, None]
        elif 'UPDATE llm_quota_ledger_reservations' in sql:
            self.rows[params[3]][8:] = ['settled', *params[:3]]
        elif 'UPDATE llm_quota_ledger_days' in sql:
            if 'settled_calls = settled_calls +' in sql:
                day = self.days[params[-1]]
                for i in range(3):
                    day[i] -= params[i]
                    day[i + 3] += params[i + 3]
            else:
                day = self.days[params[-1]]
                for i in range(3):
                    day[i] += params[i]
        else:
            raise AssertionError(sql)
        return Cursor()


@pytest.fixture
def db():
    return FakeDB()


def reserve(db, rid='id', *, day=DAY, tenant='tenant', caller='caller', amount=Amount(1, 10, '0.2'), limits=ZERO):
    return PostgresQuotaStore(db.factory).reserve(rid, day, tenant, caller, 'chat', 'default', amount, limits)


@pytest.mark.parametrize('args', [(-1, 1, '1'), (True, 1, '1'), (1, -1, '1'), (1, 1, '-1'),
                                  (1, 1, 'NaN'), (1, 1, 'Infinity'), (1, 1, 0.1)])
def test_bad_amount(args):
    with pytest.raises(ValueError):
        Amount(*args)


@pytest.mark.parametrize('kwargs', [dict(rid=''), dict(rid=' x'), dict(tenant=''), dict(caller=''),
                                    dict(day=datetime(2026, 1, 1)),
                                    dict(day=DAY.astimezone(timezone(timedelta(hours=9)))),
                                    dict(limits={}), dict(limits={**ZERO, 'global_daily_cost': 'NaN'})])
def test_bad_request(db, kwargs):
    with pytest.raises(ValueError):
        reserve(db, **kwargs)
    assert not db.events


@pytest.mark.parametrize('name,limit', [('user_daily_calls', '1'), ('user_daily_tokens', '10'),
                                      ('tenant_daily_tokens', '10'), ('tenant_daily_cost', '0.2'),
                                      ('global_daily_cost', '0.2')])
def test_five_limits_and_rollback(db, name, limit):
    reserve(db)
    before = deepcopy((db.rows, db.days))
    with pytest.raises(QuotaExceeded) as exc:
        reserve(db, 'another', limits={**ZERO, name: limit})
    assert exc.value.limit == name and exc.value.scope == LIMITS[name][0]
    assert (db.rows, db.days) == before and db.events[-1] == 'rollback'


def test_idempotency_and_collision(db):
    assert reserve(db)
    assert reserve(db) is False
    for kwargs in (dict(tenant='other'), dict(day=DAY + timedelta(days=1)), dict(amount=Amount(1, 11, '0.2'))):
        with pytest.raises(ReservationConflict):
            reserve(db, **kwargs)
    assert len(db.rows) == 1


def test_settlement_and_persistence(db):
    reserve(db)
    store = PostgresQuotaStore(db.factory)
    assert store.usage(DAY, tenant_id='tenant', caller_fingerprint='caller') == Amount(1, 10, '0.2')
    store.settle('id', Amount(2, 15, '0.3'))
    assert PostgresQuotaStore(db.factory).usage(DAY) == Amount(2, 15, '0.3')
    assert db.days[DAY] == [0, 0, Decimal(0), 2, 15, Decimal('0.3')]
    with pytest.raises(ReservationStateError):
        store.settle('id', Amount(2, 15, '0.3'))
    with pytest.raises(ReservationStateError):
        store.settle('missing', Amount(0, 0, 0))
    assert store.usage(DAY + timedelta(days=1)) == Amount(0, 0, 0)


def test_lower_settlement_and_unidentified_caller(db):
    reserve(db, caller=None, limits={**ZERO, 'user_daily_calls': 1})
    reserve(db, 'second', caller=None, limits={**ZERO, 'user_daily_calls': 1})
    PostgresQuotaStore(db.factory).settle('id', Amount(0, 2, '0.01'))
    assert PostgresQuotaStore(db.factory).usage(DAY) == Amount(1, 12, '0.21')


def test_db_failure_rolls_back_and_propagates(db):
    db.fail_on = 'UPDATE llm_quota_ledger_days'
    with pytest.raises(RuntimeError, match='database failure'):
        reserve(db)
    assert not db.rows and not db.days and db.events == ['begin', 'rollback']


def test_lock_order_and_parameterization(db):
    reserve(db, rid="id'; DROP TABLE x; --")
    sql = [s for s, _ in db.statements]
    assert 'SET TRANSACTION ISOLATION LEVEL READ COMMITTED' in sql[0]
    assert 'pg_advisory_xact_lock' in sql[1]
    assert next(i for i, s in enumerate(sql) if 'FOR UPDATE' in s) < next(i for i, s in enumerate(sql) if 'COALESCE(SUM' in s)
    assert all("id'; DROP TABLE" not in s for s in sql)
    assert any("id'; DROP TABLE" in str(p) for _, p in db.statements)


def test_schema_contract():
    sql = (Path(__file__).parents[1] / 'scripts/sql/llm_quota_ledger.sql').read_text()
    for item in ('PRIMARY KEY', 'REFERENCES llm_quota_ledger_days', 'numeric', 'CREATE INDEX', "'reserved'", "'settled'"):
        assert item in sql


def test_decimal_comparison_does_not_round(db):
    reserve(db, amount=Amount(1, 1, '1'))
    with pytest.raises(QuotaExceeded):
        reserve(db, 'second', amount=Amount(1, 1, '0.00000000000000000000000000001'),
                limits={**ZERO, 'global_daily_cost': '1'})
