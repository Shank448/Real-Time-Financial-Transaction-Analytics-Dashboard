"""Stage 1: generate synthetic payments and bulk-load them into PostgreSQL (raw zone)."""
from __future__ import annotations

import logging

from . import config as C
from . import db
from .generate_data import generate

log = logging.getLogger(__name__)


def load_raw(total_rows: int = C.DEFAULT_ROWS, seed: int = C.SEED) -> int:
    db.run_sql_file(C.SQL_DIR / "01_schema.sql")
    log.info("Generating ~%s synthetic payment records (seed=%s)...", f"{total_rows:,}", seed)
    raw, dim_route, dim_region = generate(total_rows, seed)

    db.execute("TRUNCATE raw_transactions RESTART IDENTITY; TRUNCATE dim_route; TRUNCATE dim_region;")
    db.copy_df(dim_route, "dim_route")
    db.copy_df(dim_region, "dim_region")
    db.copy_df(raw, "raw_transactions")
    n = db.scalar("SELECT COUNT(*) FROM raw_transactions")
    log.info("Loaded %s rows into raw_transactions", f"{n:,}")
    return n
