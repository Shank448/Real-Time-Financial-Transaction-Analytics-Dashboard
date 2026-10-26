"""Stage 2: extract raw rows from PostgreSQL in chunks, clean anomalies with Pandas, reload.

Rules run in a fixed order and every rule is logged (rows affected + action) to
`data_quality_log`, so the cleaning is auditable rather than a black box.

    DROP   : unusable rows (no id, unparseable / impossible timestamp, bad category,
             duplicate txn_id, missing / non-positive / statistically extreme amount)
    FIX    : standardise casing + whitespace, convert every timestamp to UTC
    NULLIFY: bad *measurements* on otherwise valid payments (latency, fraud score)
             -> row is kept, the bad value becomes NULL so it can't skew aggregates
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config as C
from . import db

log = logging.getLogger(__name__)

OUT_COLS = ["txn_id", "customer_id", "merchant_id", "event_ts", "origin_region", "dest_region",
            "payment_method", "merchant_category", "route_id", "amount_usd", "latency_ms",
            "status", "fraud_probability", "is_fraud"]


def _canon(series: pd.Series, allowed: list[str]) -> pd.Series:
    """Map messy values ('india ', 'INDIA') to canonical ones; unknown values -> NaN."""
    lookup = {v.casefold(): v for v in allowed}
    key = series.astype("string").str.strip().str.casefold()
    return key.map(lookup).astype("object")


def clean_transactions(raw: pd.DataFrame, mz_threshold: float = C.MZ_THRESHOLD):
    """Pure function: raw DataFrame in -> (clean DataFrame, audit-log DataFrame) out."""
    log_rows: list[dict] = []

    def note(rule: str, n, action: str):
        log_rows.append({"step_no": len(log_rows) + 1, "rule": rule,
                         "rows_affected": int(n), "action": action})

    df = raw.copy()
    note("rows_extracted", len(df), "input")

    # 1. missing primary key -----------------------------------------------------------
    df["txn_id"] = df["txn_id"].astype("string").str.strip()
    bad = df["txn_id"].fillna("").eq("")
    note("missing_txn_id", bad.sum(), "dropped")
    df = df[~bad]

    # 2. time-series standardisation: everything -> tz-aware UTC, second resolution -------
    #    format="ISO8601" parses 'Z', '+05:30' offsets and naive 'YYYY-MM-DD HH:MM:SS'
    #    (naive values are treated as UTC) in a single vectorised pass.
    ts = pd.to_datetime(df["event_ts_raw"], format="ISO8601", utc=True, errors="coerce")
    n_offset = df["event_ts_raw"].fillna("").str.contains(r"[+-]\d{2}:\d{2}$", regex=True).sum()
    note("timestamps_with_tz_offset_converted_to_utc", n_offset, "fixed")
    df["event_ts"] = ts.dt.floor("s")
    bad = df["event_ts"].isna()
    note("unparseable_timestamp", bad.sum(), "dropped")
    df = df[~bad]

    lo, hi = pd.Timestamp(C.VALID_START, tz="UTC"), pd.Timestamp(C.VALID_END, tz="UTC")
    bad = (df["event_ts"] < lo) | (df["event_ts"] >= hi)
    note("timestamp_outside_valid_window (epoch-zero / future)", bad.sum(), "dropped")
    df = df[~bad]

    # 3. categorical standardisation ---------------------------------------------------------
    for col, allowed in (("origin_region", C.REGIONS), ("dest_region", C.REGIONS),
                         ("payment_method", C.METHODS), ("merchant_category", C.CATEGORIES),
                         ("status", C.STATUSES)):
        fixed = _canon(df[col], allowed)
        changed = fixed.notna() & (fixed != df[col].astype("object"))
        note(f"{col}: casing/whitespace standardised", changed.sum(), "fixed")
        df[col] = fixed
        bad = df[col].isna()
        note(f"{col}: value not in canonical set", bad.sum(), "dropped")
        df = df[~bad]

    route = df["route_id"].astype("string").str.strip().str.upper()
    miss = route.fillna("").eq("")
    note("route_id missing -> 'UNKNOWN'", miss.sum(), "imputed")
    df["route_id"] = route.mask(miss, "UNKNOWN").astype("object")

    # 4. duplicates (re-delivered messages): keep the first-ingested copy -------------------
    dup = df.duplicated("txn_id", keep="first")
    note("duplicate_txn_id", dup.sum(), "dropped")
    df = df[~dup]

    # 5. amount validity ---------------------------------------------------------------------
    df["amount_usd"] = pd.to_numeric(df["amount_usd"], errors="coerce")
    bad = df["amount_usd"].isna()
    note("amount_null", bad.sum(), "dropped")
    df = df[~bad]
    bad = df["amount_usd"] <= 0
    note("amount_non_positive", bad.sum(), "dropped")
    df = df[~bad]

    #    statistical outliers: modified z-score (median/MAD) of log(amount) within each
    #    payment method. Robust to the very outliers it is trying to find.
    la = np.log(df["amount_usd"].astype(float))
    grp = df["payment_method"]
    med = la.groupby(grp).transform("median")
    mad = (la - med).abs().groupby(grp).transform("median").replace(0, np.nan)
    mz = 0.6745 * (la - med) / mad
    bad = mz > mz_threshold
    note(f"amount_extreme_outlier (modified z > {mz_threshold})", bad.sum(), "dropped")
    df = df[~bad]

    # 6. bad measurements on valid payments -> NULL, keep the row ------------------------------
    lat = pd.to_numeric(df["latency_ms"], errors="coerce")
    bad = lat.notna() & ((lat <= 0) | (lat > C.LATENCY_MAX_MS))
    note(f"latency_ms <= 0 or > {C.LATENCY_MAX_MS}", bad.sum(), "nullified")
    df["latency_ms"] = lat.mask(bad)
    note("latency_ms already null (timeouts / no measurement)", df["latency_ms"].isna().sum() - bad.sum(),
         "kept as NULL")

    fp = pd.to_numeric(df["fraud_probability"], errors="coerce")
    bad = fp.notna() & ((fp < 0) | (fp > 1))
    note("fraud_probability outside [0,1]", bad.sum(), "nullified")
    df["fraud_probability"] = fp.mask(bad)

    df["is_fraud"] = df["is_fraud"].fillna(False).astype(bool)

    # 7. final shape --------------------------------------------------------------------------
    df = df.sort_values(["event_ts", "txn_id"]).reset_index(drop=True)
    note("rows_loaded_to_transactions_clean", len(df), "output")
    return df[OUT_COLS], pd.DataFrame(log_rows)


def run_clean(chunksize: int = 250_000) -> dict:
    log.info("Extracting raw_transactions in chunks of %s...", f"{chunksize:,}")
    chunks = []
    for i, chunk in enumerate(db.iter_raw_chunks(chunksize), 1):
        chunks.append(chunk.drop(columns=["raw_id"]))
        log.info("  chunk %d: %s rows", i, f"{len(chunk):,}")
    raw = pd.concat(chunks, ignore_index=True)
    del chunks

    clean, audit = clean_transactions(raw)

    db.execute("TRUNCATE transactions_clean; TRUNCATE data_quality_log;")
    db.copy_df(clean, "transactions_clean")
    db.copy_df(audit, "data_quality_log", ["step_no", "rule", "rows_affected", "action"])
    db.execute("""
        CREATE INDEX IF NOT EXISTS ix_tc_event_ts  ON transactions_clean (event_ts);
        CREATE INDEX IF NOT EXISTS ix_tc_region_dt ON transactions_clean (origin_region, txn_date);
        CREATE INDEX IF NOT EXISTS ix_tc_route     ON transactions_clean (route_id);
        ANALYZE transactions_clean;
    """)
    log.info("transactions_clean: %s rows", f"{len(clean):,}")
    return {"rows_in": len(raw), "rows_out": len(clean), "audit": audit}
