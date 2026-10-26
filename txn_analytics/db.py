"""Thin PostgreSQL helpers: engine, templated SQL files, fast COPY loads, chunked reads."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

from . import config as C

_engine: sa.Engine | None = None

RAW_SELECT = """
SELECT raw_id, txn_id, customer_id, merchant_id, event_ts_raw, origin_region, dest_region,
       payment_method, merchant_category, route_id,
       amount_usd::float8 AS amount_usd, latency_ms::float8 AS latency_ms, status,
       fraud_probability::float8 AS fraud_probability, is_fraud
FROM raw_transactions
ORDER BY raw_id
"""


def get_engine() -> sa.Engine:
    global _engine
    if _engine is None:
        _engine = sa.create_engine(C.DATABASE_URL, pool_pre_ping=True)
    return _engine


def render_sql(text: str, params: dict | None = None) -> str:
    """Replace {{NAME}} placeholders with values from config.SQL_PARAMS (+ overrides)."""
    for k, v in {**C.SQL_PARAMS, **(params or {})}.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def run_sql_file(path: str | Path, params: dict | None = None) -> None:
    sql = render_sql(Path(path).read_text(), params)
    raw = get_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql)
        raw.commit()
    finally:
        raw.close()


def execute(sql: str) -> None:
    raw = get_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql)
        raw.commit()
    finally:
        raw.close()


def scalar(sql: str):
    with get_engine().connect() as conn:
        return conn.execute(sa.text(sql)).scalar()


def copy_df(df: pd.DataFrame, table: str, columns: list[str] | None = None) -> None:
    """Bulk-load a DataFrame with COPY (orders of magnitude faster than INSERT)."""
    cols = columns or list(df.columns)
    C.DATA_DIR.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, dir=C.DATA_DIR, newline="") as f:
        df[cols].to_csv(f, index=False, na_rep="")
        tmp = f.name
    raw = get_engine().raw_connection()
    try:
        with raw.cursor() as cur, open(tmp, newline="") as fh:
            cur.copy_expert(
                f"COPY {table} ({', '.join(cols)}) FROM STDIN WITH (FORMAT csv, HEADER true)", fh
            )
        raw.commit()
    finally:
        raw.close()
        os.unlink(tmp)


def iter_raw_chunks(chunksize: int = 250_000):
    """Stream raw_transactions out of PostgreSQL with a server-side cursor."""
    with get_engine().connect().execution_options(stream_results=True) as conn:
        yield from pd.read_sql(sa.text(RAW_SELECT), conn, chunksize=chunksize)
