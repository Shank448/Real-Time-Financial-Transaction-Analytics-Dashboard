# Real-Time Financial Transaction Analytics Dashboard

An end-to-end analytics pipeline over **1.5M+ synthetic payment transactions**: raw data lands in
PostgreSQL, Python/Pandas cleans it, SQL builds the analytical layer, and Tableau visualises it.

**Stack:** PostgreSQL 16 | Python 3.12 (Pandas, NumPy, SQLAlchemy, psycopg2) | SQL (window functions,
materialized views) | Tableau

![Sanity-check preview](reports/sanity_check_preview.png)

*The figure above is a Matplotlib sanity check generated from the SQL marts, not the Tableau
dashboard. It confirms the marts contain the patterns the dashboard is meant to surface. See
[`tableau/BUILD_GUIDE.md`](tableau/BUILD_GUIDE.md) for the dashboard build.*

## Key idea

Put each kind of work where it is cheapest and most auditable:

| Layer | Tool | Responsibility |
|---|---|---|
| Storage and heavy aggregation | PostgreSQL / SQL | percentiles, rolling windows, spike and bottleneck flags |
| Row-level cleaning logic | Pandas | parsing, standardising, validating, with an audit log |
| Presentation | Tableau | drawing pre-computed marts, interactivity |

Tableau reads ~60K aggregated rows, never the 1.5M-row fact table, so the dashboard stays fast.

## Architecture

```
generate_data.py ──COPY──> raw_transactions ──chunked read──> clean.py (Pandas)
 (dirty synthetic data)     (PostgreSQL, loose types)               │
                                                                    ▼ COPY
   Tableau <── CSV / direct ── mv_* materialized views <── sql/02-05 ── transactions_clean
                                                                        (strict types, UTC)
                                              data_quality_log  <── audit of every cleaning rule
```

## Pipeline stages

| # | Stage | Module | What it does |
|---|---|---|---|
| 1 | Load | `generate_data.py`, `load_raw.py` | Generates 1.54M raw rows with realistic structure *and* injected dirt; bulk-loads via `COPY` |
| 2 | Clean | `clean.py` | Streams raw rows out in 250K-row chunks, applies 15+ logged rules, reloads |
| 3 | Marts | `sql/02`-`05`, `marts.py` | Idempotent SQL builds materialized views |
| 4 | Export | `export.py` | Writes every mart to CSV for Tableau |
| 5 | Preview | `preview.py` | Optional sanity-check figure |

### Data cleaning (stage 2)

Every rule is written to `data_quality_log`, so cleaning is auditable.

| Rule | Action | Rows (full run) |
|---|---|---|
| Missing `txn_id` | drop | 770 |
| Unparseable timestamp (`N/A`, garbage) | drop | 1,540 |
| Timestamp outside valid window (epoch-zero, future) | drop | 3,835 |
| Duplicate `txn_id` (re-delivered messages) | drop, keep first | 7,634 |
| Amount null / non-positive | drop | 4,543 / 4,576 |
| Amount extreme outlier (modified z-score > 5 on log amount, per payment method) | drop | 2,287 |
| Timestamps carrying a non-UTC offset | converted to UTC | 183,628 |
| Region / payment-method casing and whitespace | standardised | 56,737 / 46,049 |
| Missing route | imputed as `UNKNOWN` | 3,062 |
| Latency <= 0 or > 30,000 ms | set to NULL, row kept | 4,542 |
| Fraud probability outside [0, 1] | set to NULL, row kept | 1,519 |

**Result: 1,539,999 raw rows -> 1,514,814 clean rows** (all `txn_id` unique, all UTC).

Design choices worth knowing:

1. **Time-series standardisation.** Raw timestamps arrive in three formats (`Z`, `+05:30`, naive).
   They are parsed in one vectorised ISO-8601 pass and stored as `timestamptz` in UTC. Naive values
   are assumed to be UTC.
2. **Robust outlier detection.** The modified z-score uses median/MAD, so the outliers being hunted
   can't distort the statistic that finds them. It runs on log(amount), per payment method, because
   amounts are log-normal. Of 1,086 genuine-range amounts ($10K-$1M) in the raw data, 4 were absent
   from the clean table, so the rule removes injected junk without eating the real heavy tail.
3. **Nullify vs drop.** A bad *measurement* (latency, fraud score) on a valid payment becomes NULL
   instead of dropping the payment, so volume and revenue figures stay correct.

### SQL analytics (stage 3)

| File | Output | Technique |
|---|---|---|
| `02_mart_daily_volume.sql` | `mv_daily_region_volume`, `mv_daily_total_volume` | Gap-filled calendar (`generate_series` cross join), 7-day and 30-day moving averages with `ROWS` frames, spike z-score against a trailing 28-day baseline that **excludes** the current day |
| `03_mart_latency.sql` | `mv_route_latency_*` | `percentile_cont` p50/p95/p99, 24-hour moving average over a `RANGE INTERVAL` frame, bottleneck classification |
| `04_mart_fraud.sql` | `mv_fraud_*` | `FILTER` aggregates, flag precision and recall, 7-day moving rates |
| `05_mart_flow.sql` | `mv_region_flow_weekly` | Origin-to-destination flow with hub coordinates |

**Bottleneck detection.** A route (or route + hour of day) is a bottleneck when its p95 latency
exceeds 1.75x the **median of all routes' p95**. Percentiles are used because tail latency breaks
payment SLAs, not averages. The baseline is a peer median instead of the global p95 because the
global p95 is pulled up by the very bottleneck traffic being detected (in this data: 340 ms
global vs 222 ms peer median), which hid a moderate bottleneck in testing. Routes are then
classified: `CHRONIC` (breaches in 12+ of 24 hours), `PEAK-WINDOW` (1-11 hours), `HEALTHY`.

**Spike detection.** A day-region is a spike when volume is more than 5 standard deviations above
its trailing 28-day baseline. A threshold of 3 also fired on 7 weekend days, because the baseline
mixes weekday and weekend volume. At 5 only genuine events remain.

## Results on the synthetic data

The generator plants known patterns; the SQL recovers them, which is how correctness is checked.

| Planted pattern | Recovered by SQL |
|---|---|
| 5 regional volume events (APAC Feb 17, India Mar 2, Europe Apr 17, LATAM May 9, N. America Jun 12) | Exactly those 5 flagged (z = 15.5 to 24.8), no false positives |
| 1 chronically slow route | `GW-JNB-02`: CHRONIC, breaches in 24/24 hours (p95 640 ms) |
| 3 routes congested at specific hours | `GW-SAO-01` (6 h), `GW-FRA-03` (6 h), `GW-MUM-01` (4 h): PEAK-WINDOW, matching the planted windows |
| Fraud wave with **no** volume spike | APAC Mar 21-22: fraud rate ~3.3% vs ~1.0% typical, normal volume |
| Fraud wave coinciding with a volume spike | LATAM May 9: 3.2% fraud rate |

Fraud model (simulated scores, threshold 0.70): 1.03% of transactions are confirmed fraud, 0.74%
are flagged for review, flag **precision 68.8%**, **recall 49.4%**.

## Data model

| Table | Purpose |
|---|---|
| `raw_transactions` | Landing zone, loose types, dirty data |
| `transactions_clean` | Fact table: PK, `CHECK` constraints, `timestamptz`, generated `txn_date` and `is_cross_border` |
| `dim_route`, `dim_region` | Gateway city per route; region hub coordinates |
| `data_quality_log` | Audit trail of the cleaning stage |
| `mv_*` (10 views) | Analytical marts consumed by Tableau |

## Setup and run

1. Start PostgreSQL 16 (Docker: `docker compose up -d`, which creates DB `payments`, user/password `fin`).
2. `pip install -r requirements.txt`
3. Optional: `export DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname`
4. `python run_pipeline.py` for the full pipeline (about 2-3 minutes at 1.54M rows).
5. Find the CSVs in `data/exports/`, then follow `tableau/BUILD_GUIDE.md`.

Useful variants:

| Command | Use |
|---|---|
| `python run_pipeline.py --rows 200000` | fast development run |
| `python run_pipeline.py --only marts,export` | refresh analytics only |
| `python -m pytest tests -q` | run the cleaning unit tests (no DB needed) |

To automate the refresh, schedule the marts stage, e.g. hourly with cron:
`15 * * * * cd /path/to/project && python run_pipeline.py --only marts,export`

## Repository layout

```
run_pipeline.py          orchestrator / CLI
txn_analytics/           config, db helpers, generator, loader, cleaner, marts, export, preview
sql/                     01 schema, 02-05 analytical marts (templated with {{PARAMS}} from config.py)
tests/                   pytest unit tests for every cleaning rule
tableau/BUILD_GUIDE.md   step-by-step dashboard build
reports/                 sanity-check figure
```

## Honest limitations

1. **The data is synthetic.** Patterns, anomalies and fraud scores are simulated, so the numbers
   above describe the generator, not real payment behaviour.
2. **Fraud probabilities are simulated scores**, not output from a trained model.
3. **The pipeline is batch-oriented.** Marts refresh on a schedule; there is no streaming
   ingestion, so "real-time" here means a refreshable, near-real-time design.
4. **The Tableau workbook is built by you** from the exported marts using the build guide; no
   `.twbx` file is bundled.
