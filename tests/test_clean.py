"""Unit tests for the cleaning rules (pure Pandas - no database needed)."""
import numpy as np
import pandas as pd
import pytest

from txn_analytics.clean import clean_transactions


def make_raw(n=400, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "txn_id": [f"TXN{i:06d}" for i in range(n)],
        "customer_id": "C1", "merchant_id": "M1",
        "event_ts_raw": "2026-03-01T10:00:00Z",
        "origin_region": "India", "dest_region": "India",
        "payment_method": "CARD", "merchant_category": "grocery",
        "route_id": "GW-MUM-01",
        "amount_usd": np.round(np.exp(rng.normal(3.7, 1.0, n)), 2),
        "latency_ms": 120.0, "status": "SUCCESS", "fraud_probability": 0.05, "is_fraud": False,
    })
    return df


def run(df):
    clean, audit = clean_transactions(df)
    return clean, audit.set_index("rule")["rows_affected"].to_dict()


def test_clean_input_passes_through_unchanged_in_size():
    clean, _ = run(make_raw())
    assert len(clean) == 400


def test_duplicates_keep_first_only():
    df = make_raw()
    df = pd.concat([df, df.iloc[:5]], ignore_index=True)
    clean, log = run(df)
    assert len(clean) == 400 and log["duplicate_txn_id"] == 5


def test_missing_id_and_bad_timestamps_dropped():
    df = make_raw()
    df.loc[0, "txn_id"] = None
    df.loc[1, "event_ts_raw"] = "N/A"
    df.loc[2, "event_ts_raw"] = "1970-01-01T00:00:00Z"
    df.loc[3, "event_ts_raw"] = "2027-03-14T10:00:00Z"
    clean, _ = run(df)
    assert len(clean) == 396


def test_mixed_timestamp_formats_standardised_to_utc():
    df = make_raw(5)
    df["event_ts_raw"] = ["2026-03-01T10:00:00Z", "2026-03-01T15:30:00+05:30",
                          "2026-03-01 10:00:00", "2026-03-01T10:00:00+00:00", "2026-03-01T05:00:00-05:00"]
    clean, _ = run(df)
    assert (clean["event_ts"] == pd.Timestamp("2026-03-01 10:00:00", tz="UTC")).all()
    assert str(clean["event_ts"].dt.tz) == "UTC"


def test_categorical_casing_and_whitespace_fixed():
    df = make_raw(6)
    df["origin_region"] = ["INDIA", "india ", " India", "India", "india", "InDiA"]
    df["payment_method"] = ["card", "Card ", "CARD", "card", "CARD", " card"]
    clean, _ = run(df)
    assert set(clean["origin_region"]) == {"India"} and set(clean["payment_method"]) == {"CARD"}


def test_unknown_category_dropped():
    df = make_raw(5)
    df.loc[0, "origin_region"] = "Atlantis"
    clean, _ = run(df)
    assert len(clean) == 4


def test_invalid_amounts_dropped_and_extreme_outlier_removed():
    df = make_raw()
    df.loc[0, "amount_usd"] = np.nan
    df.loc[1, "amount_usd"] = -50
    df.loc[2, "amount_usd"] = 0
    df.loc[3, "amount_usd"] = 5_000_000
    clean, log = run(df)
    assert len(clean) == 396
    assert log[[k for k in log if k.startswith("amount_extreme")][0]] == 1
    assert (clean["amount_usd"] > 0).all()


def test_bad_measurements_nullified_but_row_kept():
    df = make_raw(5)
    df["latency_ms"] = [-20.0, 999_999.0, 150.0, np.nan, 80.0]
    df["fraud_probability"] = [1.7, -0.3, 0.2, 0.5, 0.9]
    clean, _ = run(df)
    assert len(clean) == 5
    assert clean["latency_ms"].isna().sum() == 3            # 2 nullified + 1 already null
    assert clean["fraud_probability"].isna().sum() == 2


def test_missing_route_becomes_unknown():
    df = make_raw(3)
    df.loc[0, "route_id"] = None
    df.loc[1, "route_id"] = " gw-mum-01 "
    clean, _ = run(df)
    assert sorted(clean["route_id"]) == ["GW-MUM-01", "GW-MUM-01", "UNKNOWN"]


def test_output_sorted_by_time():
    df = make_raw(4)
    df["event_ts_raw"] = ["2026-03-04T00:00:00Z", "2026-03-01T00:00:00Z", "2026-03-03T00:00:00Z", "2026-03-02T00:00:00Z"]
    clean, _ = run(df)
    assert clean["event_ts"].is_monotonic_increasing
