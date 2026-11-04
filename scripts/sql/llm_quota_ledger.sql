-- Apply only to a dedicated, reviewed PostgreSQL schema; no existing tables are modified.
CREATE TABLE llm_quota_ledger_days (
    day_start timestamptz PRIMARY KEY,
    reserved_calls bigint NOT NULL DEFAULT 0 CHECK (reserved_calls >= 0),
    reserved_tokens bigint NOT NULL DEFAULT 0 CHECK (reserved_tokens >= 0),
    reserved_cost numeric NOT NULL DEFAULT 0 CHECK (reserved_cost >= 0 AND reserved_cost < 'Infinity'::numeric),
    settled_calls bigint NOT NULL DEFAULT 0 CHECK (settled_calls >= 0),
    settled_tokens bigint NOT NULL DEFAULT 0 CHECK (settled_tokens >= 0),
    settled_cost numeric NOT NULL DEFAULT 0 CHECK (settled_cost >= 0 AND settled_cost < 'Infinity'::numeric)
);

CREATE TABLE llm_quota_ledger_reservations (
    reservation_id text PRIMARY KEY CHECK (length(reservation_id) BETWEEN 1 AND 255),
    day_start timestamptz NOT NULL REFERENCES llm_quota_ledger_days(day_start),
    tenant_id text NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 255),
    caller_fingerprint text CHECK (length(caller_fingerprint) BETWEEN 1 AND 255),
    feature text NOT NULL CHECK (length(feature) BETWEEN 1 AND 255),
    profile text NOT NULL CHECK (length(profile) BETWEEN 1 AND 255),
    reserved_calls bigint NOT NULL CHECK (reserved_calls >= 0),
    reserved_tokens bigint NOT NULL CHECK (reserved_tokens >= 0),
    reserved_cost numeric NOT NULL CHECK (reserved_cost >= 0 AND reserved_cost < 'Infinity'::numeric),
    settled_calls bigint CHECK (settled_calls >= 0),
    settled_tokens bigint CHECK (settled_tokens >= 0),
    settled_cost numeric CHECK (settled_cost >= 0 AND settled_cost < 'Infinity'::numeric),
    status text NOT NULL CHECK (status IN ('reserved', 'settled')),
    CHECK ((status = 'reserved' AND settled_calls IS NULL AND settled_tokens IS NULL AND settled_cost IS NULL)
        OR (status = 'settled' AND settled_calls IS NOT NULL AND settled_tokens IS NOT NULL AND settled_cost IS NOT NULL))
);
CREATE INDEX llm_quota_ledger_tenant_day_idx ON llm_quota_ledger_reservations (day_start, tenant_id, caller_fingerprint);
