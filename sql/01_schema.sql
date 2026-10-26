-- 01_schema.sql : tables for the raw landing zone, cleaned fact table and audit log.
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS dim_route (
    route_id      TEXT PRIMARY KEY,
    gateway_city  TEXT NOT NULL,
    region        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_region (
    region   TEXT PRIMARY KEY,
    hub_lat  DOUBLE PRECISION NOT NULL,
    hub_lon  DOUBLE PRECISION NOT NULL
);

-- Landing zone: deliberately loose types so dirty data can be loaded without errors.
CREATE TABLE IF NOT EXISTS raw_transactions (
    raw_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- ingest order
    txn_id             TEXT,
    customer_id        TEXT,
    merchant_id        TEXT,
    event_ts_raw       TEXT,            -- mixed formats / offsets / garbage
    origin_region      TEXT,
    dest_region        TEXT,
    payment_method     TEXT,
    merchant_category  TEXT,
    route_id           TEXT,
    amount_usd         NUMERIC(16,2),
    latency_ms         NUMERIC(12,1),
    status             TEXT,
    fraud_probability  NUMERIC(10,5),
    is_fraud           BOOLEAN
);

-- Analytics-ready fact table: strict types, constraints, UTC timestamps.
CREATE TABLE IF NOT EXISTS transactions_clean (
    txn_id             TEXT PRIMARY KEY,
    customer_id        TEXT,
    merchant_id        TEXT,
    event_ts           TIMESTAMPTZ NOT NULL,                                   -- always UTC-normalised
    txn_date           DATE GENERATED ALWAYS AS ((event_ts AT TIME ZONE 'UTC')::date) STORED,
    origin_region      TEXT NOT NULL,
    dest_region        TEXT NOT NULL,
    is_cross_border    BOOLEAN GENERATED ALWAYS AS (origin_region <> dest_region) STORED,
    payment_method     TEXT NOT NULL,
    merchant_category  TEXT NOT NULL,
    route_id           TEXT NOT NULL,                                          -- 'UNKNOWN' if missing
    amount_usd         NUMERIC(16,2) NOT NULL CHECK (amount_usd > 0),
    latency_ms         REAL CHECK (latency_ms IS NULL OR latency_ms > 0),
    status             TEXT NOT NULL CHECK (status IN ('SUCCESS','FAILED','PENDING','REVERSED')),
    fraud_probability  REAL CHECK (fraud_probability IS NULL OR fraud_probability BETWEEN 0 AND 1),
    is_fraud           BOOLEAN NOT NULL
);

-- Audit trail: what the cleaning stage did, per run.
CREATE TABLE IF NOT EXISTS data_quality_log (
    run_ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    step_no        INT,
    rule           TEXT NOT NULL,
    rows_affected  BIGINT NOT NULL,
    action         TEXT NOT NULL
);
