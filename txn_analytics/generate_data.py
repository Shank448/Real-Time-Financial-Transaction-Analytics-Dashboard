"""Synthetic payment-transaction generator.

Produces a *dirty* raw dataset on purpose, so the cleaning stage has real work to do:

    clean signal  : diurnal + weekly seasonality, growth trend, regional volume spikes,
                    route-level latency bottlenecks, fraud waves
    injected noise: duplicates, null/negative/extreme amounts, mixed-format and
                    out-of-range timestamps, inconsistent casing, bad latency values

Everything is vectorised NumPy, so 1.5M+ rows generate in well under a minute.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

REGIONS = C.REGIONS
REGION_W = np.array([0.28, 0.24, 0.18, 0.15, 0.09, 0.06])
UTC_OFFSET_H = np.array([-5.0, 1.0, 5.5, 8.0, -3.0, 3.0])   # local-time diurnal curve

# route_id, gateway city, region, base latency ms, log-sigma, congestion mult,
# congestion window (UTC start hour, length in hours), routing weight inside region
ROUTES = [
    ("GW-NYC-01", "New York",      "North America",  85, 0.35, 1.0, (0, 0),   0.55),
    ("GW-SFO-02", "San Francisco", "North America",  95, 0.35, 1.0, (0, 0),   0.45),
    ("GW-FRA-01", "Frankfurt",     "Europe",         90, 0.35, 1.0, (0, 0),   0.40),
    ("GW-LON-02", "London",        "Europe",         80, 0.35, 1.0, (0, 0),   0.40),
    ("GW-FRA-03", "Frankfurt",     "Europe",        110, 0.40, 3.2, (11, 6),  0.20),  # peak-hour bottleneck
    ("GW-MUM-01", "Mumbai",        "India",         105, 0.38, 2.4, (6, 4),   0.55),  # peak-hour bottleneck
    ("GW-BLR-02", "Bengaluru",     "India",         100, 0.35, 1.0, (0, 0),   0.45),
    ("GW-SIN-01", "Singapore",     "APAC",          120, 0.35, 1.0, (0, 0),   0.40),
    ("GW-SYD-02", "Sydney",        "APAC",          130, 0.35, 1.0, (0, 0),   0.30),
    ("GW-TYO-03", "Tokyo",         "APAC",          115, 0.35, 1.0, (0, 0),   0.30),
    ("GW-SAO-01", "Sao Paulo",     "LATAM",         150, 0.42, 2.6, (20, 6),  1.00),  # evening bottleneck
    ("GW-DXB-01", "Dubai",         "MEA",           140, 0.40, 1.0, (0, 0),   0.55),
    ("GW-JNB-02", "Johannesburg",  "MEA",           230, 0.60, 1.0, (0, 0),   0.45),  # chronically slow
]

# (region, date, volume multiplier)  - e.g. a sale day / festival
SPIKE_EVENTS = [
    ("APAC", "2026-02-17", 2.5),
    ("India", "2026-03-02", 2.6),
    ("Europe", "2026-04-17", 2.2),
    ("LATAM", "2026-05-09", 3.0),
    ("North America", "2026-06-12", 2.4),
]
# fraud-rate multipliers; the APAC wave has NO matching volume spike (a fraud ring)
FRAUD_WAVES = [
    ("LATAM", "2026-05-09", 3.0),
    ("APAC", "2026-03-21", 4.0),
    ("APAC", "2026-03-22", 4.0),
]

CAT_W = np.array([0.20, 0.10, 0.10, 0.15, 0.10, 0.10, 0.08, 0.12, 0.05])
CAT_W = CAT_W / CAT_W.sum()
CAT_FRAUD_MULT = np.array([0.5, 1.2, 2.0, 0.6, 0.3, 1.0, 2.0, 0.7, 4.0])
CAT_AMT_MULT = np.array([1.0, 3.0, 4.0, 0.6, 1.5, 1.2, 0.8, 0.5, 3.5])

# per payment method: lognormal (mu, sigma) of amount in USD
PM_MU = np.array([3.7, 2.8, 3.0, 5.5, 4.6])
PM_SIGMA = np.array([1.0, 0.9, 0.8, 1.1, 0.7])

ANOMALY_RATES = {
    "dup_rows": 0.005, "null_txn_id": 0.0005, "null_amount": 0.003,
    "nonpositive_amount": 0.003, "extreme_amount": 0.0015,
    "future_ts": 0.0015, "epoch_ts": 0.001, "garbage_ts": 0.001,
    "region_casing": 0.04, "method_casing": 0.03, "null_route": 0.002,
    "null_latency": 0.01, "negative_latency": 0.002, "absurd_latency": 0.001,
    "bad_fraud_prob": 0.001,
}


def _event_matrix(events, n_days, decay=0.0):
    m = np.ones((n_days, len(REGIONS)))
    t0 = pd.Timestamp(C.START_DATE)
    for region, date, mult in events:
        d = (pd.Timestamp(date) - t0).days
        r = REGIONS.index(region)
        m[d, r] = max(m[d, r], mult)
        if decay and d + 1 < n_days:
            m[d + 1, r] = max(m[d + 1, r], 1 + (mult - 1) * decay)
    return m


def _diurnal_probs():
    h = np.arange(24)
    w = 0.25 + np.exp(-((h - 13) ** 2) / (2 * 4.0 ** 2)) + 0.8 * np.exp(-((h - 20) ** 2) / (2 * 2.5 ** 2))
    return w / w.sum()


def dim_route_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [(r[0], r[1], r[2]) for r in ROUTES], columns=["route_id", "gateway_city", "region"]
    )


def dim_region_frame() -> pd.DataFrame:
    hubs = {"North America": (39.8, -98.6), "Europe": (50.1, 8.7), "India": (19.1, 72.9),
            "APAC": (1.35, 103.8), "LATAM": (-23.5, -46.6), "MEA": (25.2, 55.3)}
    return pd.DataFrame(
        [(r, hubs[r][0], hubs[r][1]) for r in REGIONS], columns=["region", "hub_lat", "hub_lon"]
    )


def generate_clean_signal(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n_days, n_reg = C.N_DAYS, len(REGIONS)
    t0 = pd.Timestamp(C.START_DATE, tz="UTC")
    start_epoch = int(t0.timestamp())

    # ---- when & where: (day, region) grid with seasonality, growth, spikes ----
    days = pd.date_range(C.START_DATE, periods=n_days, freq="D")
    weekly = np.where(days.dayofweek >= 5, 1.12, np.where(days.dayofweek == 4, 1.05, 1.0))
    growth = np.linspace(1.0, 1.25, n_days)
    day_w = weekly * growth * rng.normal(1.0, 0.04, n_days)
    vol_mult = _event_matrix(SPIKE_EVENTS, n_days, decay=0.3)
    p = day_w[:, None] * REGION_W[None, :] * vol_mult
    cell = rng.choice(n_days * n_reg, size=n, p=(p / p.sum()).ravel())
    day_idx, reg_idx = cell // n_reg, cell % n_reg

    local_hour = rng.choice(24, size=n, p=_diurnal_probs())
    local_sec = local_hour * 3600 + rng.integers(0, 3600, n)
    off = (UTC_OFFSET_H[reg_idx] * 3600).astype(np.int64)
    total = n_days * 86400
    ts_epoch = start_epoch + np.mod(day_idx * 86400 + local_sec - off, total)
    utc_hour = (np.mod(ts_epoch, 86400) // 3600).astype(int)

    # ---- who / what ----
    methods = np.array(C.METHODS)
    base_w = np.array([0.50, 0.0, 0.20, 0.20, 0.10])
    pm_idx = np.empty(n, dtype=np.int8)
    for r, name in enumerate(REGIONS):
        m = np.flatnonzero(reg_idx == r)
        w = np.array([0.25, 0.45, 0.15, 0.10, 0.05]) if name == "India" else base_w
        pm_idx[m] = rng.choice(len(methods), size=m.size, p=w)
    cat_idx = rng.choice(len(C.CATEGORIES), size=n, p=CAT_W)

    amount = np.exp(PM_MU[pm_idx] + PM_SIGMA[pm_idx] * rng.standard_normal(n)) * CAT_AMT_MULT[cat_idx]
    amount = np.maximum(amount, 0.5)

    # ---- fraud ground truth + a (simulated) model score ----
    fraud_mult = _event_matrix(FRAUD_WAVES, n_days)
    p_fraud = 0.008 * CAT_FRAUD_MULT[cat_idx]
    p_fraud = p_fraud * np.where(local_hour <= 4, 2.0, 1.0) * np.where(amount > 500, 1.8, 1.0)
    p_fraud = np.clip(p_fraud * fraud_mult[day_idx, reg_idx], 0, 0.25)
    is_fraud = rng.random(n) < p_fraud
    amount = np.where(is_fraud, amount * rng.lognormal(0.5, 0.5, n), amount)
    #   scores overlap on purpose: 10% of frauds look benign, 1.5% of legit payments look suspicious
    score_fraud = np.where(rng.random(n) < 0.10, rng.beta(1.5, 6, n), rng.beta(6, 2.5, n))
    score_legit = np.where(rng.random(n) < 0.015, rng.beta(3, 3, n), rng.beta(0.9, 14, n))
    fraud_prob = np.where(is_fraud, score_fraud, score_legit)

    # ---- routing + latency ----
    route_idx = np.empty(n, dtype=np.int16)
    for r, name in enumerate(REGIONS):
        ids = [i for i, rt in enumerate(ROUTES) if rt[2] == name]
        w = np.array([ROUTES[i][7] for i in ids])
        m = np.flatnonzero(reg_idx == r)
        route_idx[m] = rng.choice(ids, size=m.size, p=w / w.sum())
    base = np.array([r[3] for r in ROUTES], dtype=float)[route_idx]
    sig = np.array([r[4] for r in ROUTES])[route_idx]
    cong = np.array([r[5] for r in ROUTES])[route_idx]
    w_start = np.array([r[6][0] for r in ROUTES])[route_idx]
    w_len = np.array([r[6][1] for r in ROUTES])[route_idx]
    in_window = np.mod(utc_hour - w_start, 24) < w_len
    lat = base * np.exp(sig * rng.standard_normal(n))
    lat = lat * np.where(in_window, cong, 1.0)
    lat = lat * (1 + 0.15 * (vol_mult[day_idx, reg_idx] - 1))      # load -> latency
    tail = rng.random(n) < 0.01
    lat[tail] *= 4
    lat = np.maximum(np.rint(lat), 5.0)

    # ---- status (slow requests fail more often) ----
    p_fail = 0.02 + np.where(lat > 1500, 0.12, 0.0) + np.where(lat > 3000, 0.20, 0.0)
    status = np.where(rng.random(n) < p_fail, "FAILED", "SUCCESS").astype(object)
    ok = status == "SUCCESS"
    status[ok & (rng.random(n) < 0.006)] = "PENDING"
    ok = status == "SUCCESS"
    status[ok & ((rng.random(n) < 0.008) | (is_fraud & (rng.random(n) < 0.6)))] = "REVERSED"

    # ---- destination (15% cross-border) ----
    dest_idx = reg_idx.copy()
    xb = rng.random(n) < 0.15
    dest_idx[xb] = rng.choice(n_reg, size=xb.sum(), p=REGION_W)

    # ---- sort by time, assign ids ----
    order = np.argsort(ts_epoch, kind="stable")
    take = lambda a: a[order]  # noqa: E731
    ts_epoch = take(ts_epoch)
    df = pd.DataFrame({
        "txn_id": "TXN" + pd.Series(np.arange(1, n + 1)).astype(str).str.zfill(10).to_numpy(),
        "customer_id": "C" + rng.integers(1, 400_001, n).astype(str),
        "merchant_id": "M" + rng.integers(1, 25_001, n).astype(str),
        "_ts_epoch": ts_epoch,
        "origin_region": np.array(REGIONS, dtype=object)[take(reg_idx)],
        "dest_region": np.array(REGIONS, dtype=object)[take(dest_idx)],
        "payment_method": methods.astype(object)[take(pm_idx)],
        "merchant_category": np.array(C.CATEGORIES, dtype=object)[take(cat_idx)],
        "route_id": np.array([r[0] for r in ROUTES], dtype=object)[take(route_idx)],
        "amount_usd": np.round(take(amount), 2),
        "latency_ms": take(lat),
        "status": take(status),
        "fraud_probability": np.round(take(fraud_prob), 5),
        "is_fraud": take(is_fraud),
    })
    return df


def _format_timestamps(ts_epoch: np.ndarray, rng) -> np.ndarray:
    """80% ISO-8601 UTC 'Z', 12% ISO with +05:30 offset, 8% naive 'YYYY-MM-DD HH:MM:SS' (UTC)."""
    dt = ts_epoch.astype("datetime64[s]")
    utc_s = np.datetime_as_string(dt, unit="s")
    ist_s = np.datetime_as_string(dt + np.timedelta64(19800, "s"), unit="s")
    kind = rng.choice(3, size=len(dt), p=[0.80, 0.12, 0.08])
    return np.where(
        kind == 0, np.char.add(utc_s, "Z"),
        np.where(kind == 1, np.char.add(ist_s, "+05:30"), np.char.replace(utc_s, "T", " ")),
    ).astype(object)


def inject_anomalies(df: pd.DataFrame, rng) -> pd.DataFrame:
    n = len(df)
    R = ANOMALY_RATES

    def pick(key):
        return rng.choice(n, size=int(n * R[key]), replace=False)

    df["event_ts_raw"] = _format_timestamps(df.pop("_ts_epoch").to_numpy(), rng)

    df.loc[pick("null_txn_id"), "txn_id"] = None
    df.loc[pick("null_amount"), "amount_usd"] = np.nan
    i = pick("nonpositive_amount")
    df.loc[i, "amount_usd"] = rng.choice([0.0, -1.0, -25.5, -300.0], size=len(i))
    i = pick("extreme_amount")
    df.loc[i, "amount_usd"] = np.round(rng.uniform(1e6, 1e7, len(i)), 2)

    df.loc[pick("future_ts"), "event_ts_raw"] = "2027-03-14T10:00:00Z"
    df.loc[pick("epoch_ts"), "event_ts_raw"] = "1970-01-01T00:00:00Z"
    i = pick("garbage_ts")
    df.loc[i, "event_ts_raw"] = rng.choice(["N/A", "not_a_timestamp", "0000-00-00"], size=len(i))

    for col, key, variants in (
        ("origin_region", "region_casing", lambda v: [v.upper(), v.lower(), v + " ", " " + v]),
        ("dest_region", "region_casing", lambda v: [v.upper(), v.lower(), v + " ", " " + v]),
        ("payment_method", "method_casing", lambda v: [v.lower(), v.title(), v + " "]),
    ):
        idx = pick(key)
        vals = df[col].to_numpy(dtype=object)
        ch = rng.integers(0, 100, len(idx))
        for j, k in zip(idx, ch):
            opts = variants(vals[j])
            vals[j] = opts[k % len(opts)]
        df[col] = vals

    df.loc[pick("null_route"), "route_id"] = None
    df.loc[pick("null_latency"), "latency_ms"] = np.nan
    i = pick("negative_latency")
    df.loc[i, "latency_ms"] = -rng.integers(1, 500, len(i))
    i = pick("absurd_latency")
    df.loc[i, "latency_ms"] = rng.integers(60_000, 900_000, len(i))
    i = pick("bad_fraud_prob")
    df.loc[i, "fraud_probability"] = rng.choice([1.7, -0.3, 9.99], size=len(i))

    dups = df.iloc[pick("dup_rows")].copy()            # re-delivered messages
    return pd.concat([df, dups], ignore_index=True)


def generate(total_rows: int = C.DEFAULT_ROWS, seed: int = C.SEED):
    """Return (raw_transactions_df, dim_route_df, dim_region_df)."""
    rng = np.random.default_rng(seed + 1)
    n_base = int(total_rows / (1 + ANOMALY_RATES["dup_rows"]))
    df = generate_clean_signal(n_base, seed)
    raw = inject_anomalies(df, rng)
    cols = ["txn_id", "customer_id", "merchant_id", "event_ts_raw", "origin_region", "dest_region",
            "payment_method", "merchant_category", "route_id", "amount_usd", "latency_ms",
            "status", "fraud_probability", "is_fraud"]
    return raw[cols], dim_route_frame(), dim_region_frame()
