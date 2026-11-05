"""Unconnected PostgreSQL reservation ledger.

The factory must return a context manager yielding a psycopg 3 connection (e.g.
state_backend.postgres). Each operation owns a transaction; use a fresh, idle
connection per operation. Limits are already resolved by the caller: zero means
unlimited. Usage is outstanding reservations plus settled actual usage.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Mapping

from rag_core.llm_quota import LIMITS, QuotaExceeded


class ReservationConflict(ValueError):
    """The reservation ID already belongs to a different request."""


class ReservationStateError(RuntimeError):
    """The reservation is missing or cannot be settled in its current state."""


def _id(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 255 or '\x00' in value:
        raise ValueError(f"invalid {name}")
    return value


def _day(value):
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("day_start must be an aware UTC datetime")
    return value


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 9223372036854775807:
        raise ValueError("calls and tokens must be nonnegative bigint integers")
    return value


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise ValueError("cost and limits must be exact decimal values")
    try:
        result = Decimal(value)
    except (ValueError, ArithmeticError):
        raise ValueError("invalid decimal") from None
    if not result.is_finite() or result < 0:
        raise ValueError("decimal must be finite and nonnegative")
    # PostgreSQL unconstrained NUMERIC can silently round beyond its maximum
    # fractional scale. Ignore insignificant trailing zeros when checking it.
    if result:
        digits = result.as_tuple().digits
        trailing = 0
        for digit in reversed(digits):
            if digit:
                break
            trailing += 1
        if result.adjusted() >= 131072 or result.as_tuple().exponent + trailing < -16383:
            raise ValueError("decimal exceeds PostgreSQL numeric precision")
    return result


@dataclass(frozen=True)
class Amount:
    calls: int
    tokens: int
    cost: Decimal

    def __post_init__(self):
        _integer(self.calls)
        _integer(self.tokens)
        object.__setattr__(self, "cost", _decimal(self.cost))


class PostgresQuotaStore:
    def __init__(self, connection_factory):
        self._factory = connection_factory

    @staticmethod
    def _require_idle(conn):
        # A nested transaction is a savepoint and cannot own the isolation level.
        info = getattr(conn, "info", None)
        if info is not None and info.transaction_status != 0:
            raise RuntimeError("quota store requires an idle connection")

    @staticmethod
    def _lock_id(conn, reservation_id):
        # Same ID is serialized even when competing requests specify different days.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (reservation_id,))

    @staticmethod
    def _lock_day(conn, day):
        # One lock per day serializes all scopes, including first-row creation.
        conn.execute("INSERT INTO llm_quota_ledger_days (day_start) VALUES (%s) ON CONFLICT DO NOTHING", (day,))
        conn.execute("SELECT day_start FROM llm_quota_ledger_days WHERE day_start = %s FOR UPDATE", (day,)).fetchone()

    @staticmethod
    def _usage(conn, day, tenant, caller):
        row = conn.execute("""SELECT
            COALESCE(SUM(CASE WHEN status = 'reserved' THEN reserved_calls ELSE settled_calls END), 0),
            COALESCE(SUM(CASE WHEN status = 'reserved' THEN reserved_tokens ELSE settled_tokens END), 0),
            COALESCE(SUM(CASE WHEN status = 'reserved' THEN reserved_cost ELSE settled_cost END), 0)
            FROM llm_quota_ledger_reservations WHERE day_start = %s
            AND (%s::text IS NULL OR tenant_id = %s)
            AND (%s::text IS NULL OR caller_fingerprint = %s)""", (day, tenant, tenant, caller, caller)).fetchone()
        return Amount(int(row[0]), int(row[1]), row[2])

    def usage(self, day_start, *, tenant_id=None, caller_fingerprint=None):
        day = _day(day_start)
        if tenant_id is not None:
            _id(tenant_id, "tenant_id")
        if caller_fingerprint is not None:
            _id(caller_fingerprint, "caller_fingerprint")
            if tenant_id is None:
                raise ValueError("caller requires tenant_id")
        with self._factory() as conn:
            self._require_idle(conn)
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                return self._usage(conn, day, tenant_id, caller_fingerprint)

    def reserve(self, reservation_id, day_start, tenant_id, caller_fingerprint, feature, profile, amount: Amount, limits: Mapping):
        rid = _id(reservation_id, "reservation_id")
        day = _day(day_start)
        tenant = _id(tenant_id, "tenant_id")
        caller = None if caller_fingerprint is None else _id(caller_fingerprint, "caller_fingerprint")
        _id(feature, "feature")
        _id(profile, "profile")
        if not isinstance(amount, Amount) or set(limits) != set(LIMITS):
            raise ValueError("amount and all five resolved limits are required")
        resolved = {name: _decimal(value) for name, value in limits.items()}
        request = (day, tenant, caller, feature, profile, amount.calls, amount.tokens, amount.cost)
        with self._factory() as conn:
            self._require_idle(conn)
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                self._lock_id(conn, rid)
                row = conn.execute("""SELECT day_start, tenant_id, caller_fingerprint, feature, profile,
                    reserved_calls, reserved_tokens, reserved_cost FROM llm_quota_ledger_reservations
                    WHERE reservation_id = %s""", (rid,)).fetchone()
                if row is not None:
                    if tuple(row) != request:
                        raise ReservationConflict("reservation ID request mismatch")
                    return False
                self._lock_day(conn, day)
                scopes = {'global': self._usage(conn, day, None, None),
                          'tenant': self._usage(conn, day, tenant, None)}
                if caller is not None:
                    scopes['user'] = self._usage(conn, day, tenant, caller)
                for name, (scope, metric) in LIMITS.items():
                    limit = resolved[name]
                    if limit and scope in scopes:
                        used = getattr(scopes[scope], metric)
                        if Fraction(used) + Fraction(getattr(amount, metric)) > Fraction(limit):
                            raise QuotaExceeded(name, scope, limit, used)
                conn.execute("""INSERT INTO llm_quota_ledger_reservations
                    (reservation_id, day_start, tenant_id, caller_fingerprint, feature, profile,
                     reserved_calls, reserved_tokens, reserved_cost, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'reserved')""", (rid, *request))
                conn.execute("""UPDATE llm_quota_ledger_days SET reserved_calls = reserved_calls + %s,
                    reserved_tokens = reserved_tokens + %s, reserved_cost = reserved_cost + %s WHERE day_start = %s""",
                    (amount.calls, amount.tokens, amount.cost, day))
                return True

    def settle(self, reservation_id, amount: Amount):
        rid = _id(reservation_id, "reservation_id")
        if not isinstance(amount, Amount):
            raise ValueError("amount must be Amount")
        with self._factory() as conn:
            self._require_idle(conn)
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                self._lock_id(conn, rid)
                row = conn.execute("""SELECT day_start, reserved_calls, reserved_tokens, reserved_cost, status
                    FROM llm_quota_ledger_reservations WHERE reservation_id = %s FOR UPDATE""", (rid,)).fetchone()
                if row is None or row[4] != 'reserved':
                    raise ReservationStateError("reservation missing or already settled")
                day, calls, tokens, cost, _ = row
                self._lock_day(conn, day)
                conn.execute("""UPDATE llm_quota_ledger_reservations SET status = 'settled', settled_calls = %s,
                    settled_tokens = %s, settled_cost = %s WHERE reservation_id = %s""",
                    (amount.calls, amount.tokens, amount.cost, rid))
                conn.execute("""UPDATE llm_quota_ledger_days SET reserved_calls = reserved_calls - %s,
                    reserved_tokens = reserved_tokens - %s, reserved_cost = reserved_cost - %s,
                    settled_calls = settled_calls + %s, settled_tokens = settled_tokens + %s,
                    settled_cost = settled_cost + %s WHERE day_start = %s""",
                    (calls, tokens, cost, amount.calls, amount.tokens, amount.cost, day))
