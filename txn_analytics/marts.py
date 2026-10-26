"""Stage 3: run the analytical SQL files in order and (re)build the materialized views.

The SQL files are idempotent (DROP ... CREATE), so this stage can be scheduled, e.g. cron:
    15 * * * *  cd /path/to/project && python run_pipeline.py --only marts,export
"""
from __future__ import annotations

import logging
import time

from . import config as C
from . import db

log = logging.getLogger(__name__)


def list_matviews() -> list[str]:
    import sqlalchemy as sa
    with db.get_engine().connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT matviewname FROM pg_matviews WHERE schemaname = 'public' ORDER BY matviewname"))
        return [r[0] for r in rows]


def run_marts() -> dict[str, int]:
    for path in sorted(C.SQL_DIR.glob("0[2-9]_*.sql")):
        t0 = time.time()
        db.run_sql_file(path)
        log.info("  ran %-28s %5.1fs", path.name, time.time() - t0)
    counts = {mv: db.scalar(f"SELECT COUNT(*) FROM {mv}") for mv in list_matviews()}
    for mv, n in counts.items():
        log.info("  %-28s %9s rows", mv, f"{n:,}")
    return counts
