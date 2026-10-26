"""Optional sanity-check figure built from the marts (NOT the Tableau dashboard - it just lets
you eyeball that spikes, bottlenecks and fraud waves came out of the SQL as expected)."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from . import config as C
from . import db


def make_preview(path=None):
    eng = db.get_engine()
    vol = pd.read_sql("SELECT * FROM mv_daily_region_volume", eng, parse_dates=["txn_date"])
    tot = pd.read_sql("SELECT * FROM mv_daily_total_volume", eng, parse_dates=["txn_date"])
    lat = pd.read_sql("SELECT * FROM mv_route_latency_summary ORDER BY p95_ms DESC", eng)
    hod = pd.read_sql("SELECT route_id, hour_utc, p95_ms FROM mv_route_latency_hod", eng)
    fr = pd.read_sql("SELECT * FROM mv_fraud_daily_region", eng, parse_dates=["txn_date"])

    fig, ax = plt.subplots(2, 2, figsize=(16, 9.5))
    a = ax[0, 0]
    a.bar(tot.txn_date, tot.txn_count, color="#c9d6e8", width=1.0, label="daily")
    a.plot(tot.txn_date, tot.txn_count_ma7, color="#1f4e79", lw=2, label="7-day MA")
    a.plot(tot.txn_date, tot.txn_count_ma30, color="#d95f02", lw=2, label="30-day MA")
    sp = vol[vol.is_spike]
    a.scatter(sp.txn_date, tot.set_index("txn_date").loc[sp.txn_date, "txn_count"], color="red", zorder=5, label="regional spike")
    a.set_title("Daily transactions, moving averages, regional spikes"); a.legend()

    a = ax[0, 1]
    colors = {"CHRONIC": "#c0392b", "PEAK-WINDOW": "#e67e22", "HEALTHY": "#27ae60"}
    a.barh(lat.route_id[::-1], lat.p95_ms[::-1], color=[colors[t] for t in lat.bottleneck_type[::-1]])
    g = db.scalar("SELECT peer_p95_ms FROM mv_global_latency")
    a.axvline(g * C.SQL_PARAMS["LATENCY_BREACH_MULT"], ls="--", color="k", label="breach threshold (x peer median p95)")
    a.set_title("p95 latency by route (ms) - colour = bottleneck type"); a.legend(loc="lower right")

    a = ax[1, 0]
    piv = hod.pivot(index="route_id", columns="hour_utc", values="p95_ms")
    im = a.imshow(piv.values, aspect="auto", cmap="YlOrRd")
    a.set_yticks(range(len(piv))); a.set_yticklabels(piv.index); a.set_xticks(range(0, 24, 3)); a.set_xticklabels(range(0, 24, 3))
    a.set_title("p95 latency: route x UTC hour"); fig.colorbar(im, ax=a)

    a = ax[1, 1]
    for r, g_ in fr.groupby("origin_region"):
        a.plot(g_.txn_date, g_.flagged_rate_ma7 * 100, label=r)
    a.set_title("Flagged-for-review rate (7-day MA, %)"); a.legend(ncol=3, fontsize=8)
    fig.tight_layout()
    out = path or (C.REPORT_DIR / "sanity_check_preview.png")
    C.REPORT_DIR.mkdir(exist_ok=True)
    fig.savefig(out, dpi=110)
    return out
