"""Stage 4: export the analytical marts as CSVs - ready to drop into Tableau
(Tableau Public cannot connect to PostgreSQL directly; Tableau Desktop can use either)."""
from __future__ import annotations

import logging

from . import config as C
from . import db
from .marts import list_matviews

log = logging.getLogger(__name__)


def export_marts() -> list[str]:
    C.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    raw = db.get_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            for name in list_matviews() + ["dim_region", "dim_route", "data_quality_log"]:
                path = C.EXPORT_DIR / f"{name}.csv"
                with open(path, "w", newline="") as fh:
                    cur.copy_expert(f"COPY (SELECT * FROM {name}) TO STDOUT WITH (FORMAT csv, HEADER true)", fh)
                written.append(path.name)
                log.info("  exported %s", path.name)
    finally:
        raw.close()
    return written
