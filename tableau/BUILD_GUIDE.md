# Building the Tableau dashboard

The pipeline produces small, pre-aggregated marts so Tableau stays fast: the largest file is
about 56K rows, versus 1.5M in the fact table. Run `python run_pipeline.py` first; the CSVs
land in `data/exports/`.

**Key idea:** the heavy lifting (cleaning, rolling averages, percentiles, spike and bottleneck
flags) is done in SQL. Tableau only has to *draw* it. Don't recompute rolling averages in
Tableau table calcs when the mart already holds them.

## 1. Connect the data

| Option | When to use | How |
|---|---|---|
| CSV files | Tableau Public, or no DB access | Data > New Data Source > Text File, add each CSV from `data/exports/` |
| PostgreSQL | Tableau Desktop | Connect > PostgreSQL, server `localhost`, DB `payments`, pick the `mv_*` views |

Each mart is its own data source (they have different grains). Don't join them.

## 2. Data sources and what each one feeds

| Data source | Grain | Feeds |
|---|---|---|
| `mv_daily_total_volume` | day | headline trend + moving averages |
| `mv_daily_region_volume` | day x region | regional spike heatmap |
| `mv_region_flow_weekly` | week x origin x destination | transaction flow map / matrix |
| `mv_route_latency_summary` | route | bottleneck ranking |
| `mv_route_latency_hod` | route x UTC hour | latency heatmap |
| `mv_route_latency_hourly` | route x hour | latency time series with 24h MA |
| `mv_fraud_daily_region` | day x region | fraud trend, flagged rate |
| `mv_fraud_score_bands` | band x region x label | score histogram |
| `mv_fraud_segments` | region x category x method | risk concentration treemap |

## 3. Worksheets (numbered build steps)

1. **KPI strip** (`mv_daily_region_volume`): four text tiles.
   Transactions `SUM([Txn Count])`, Volume `SUM([Total Amount Usd])`,
   Success rate `SUM([Success Rate]*[Txn Count])/SUM([Txn Count])`,
   Spike days `COUNT(IF [Is Spike] THEN 1 END)`.
2. **Volume trend** (`mv_daily_total_volume`): Columns `Txn Date` (continuous day); Rows
   `Txn Count`, `Txn Count Ma7`, `Txn Count Ma30` as a Measure Values line. Make the raw count
   a light-grey bar (dual axis) so the moving averages read clearly.
3. **Regional spike heatmap** (`mv_daily_region_volume`): Columns `Txn Date` (week), Rows
   `Origin Region`, Colour `Volume Zscore` (diverging). Add a second mark layer: circles filtered
   to `Is Spike = TRUE`, so the five events stand out.
4. **Transaction flow map** (`mv_region_flow_weekly`): create the calculated field
   `Flow Line = MAKELINE(MAKEPOINT([Origin Lat],[Origin Lon]), MAKEPOINT([Dest Lat],[Dest Lon]))`.
   Drop it on Detail, size by `SUM([Txn Count])`, filter `Is Cross Border = TRUE` (intra-region
   flow dwarfs everything else). Backup if maps misbehave: a matrix with `Origin Region` on Rows,
   `Dest Region` on Columns, and `Txn Count` as colour.
5. **Bottleneck ranking** (`mv_route_latency_summary`): horizontal bars, Rows `Route Id` sorted
   by `P95 Ms` descending, colour by `Bottleneck Type`
   (CHRONIC red / PEAK-WINDOW orange / HEALTHY green), reference line at the threshold.
6. **Latency heatmap** (`mv_route_latency_hod`): Columns `Hour Utc`, Rows `Route Id`, colour
   `P95 Ms`. Add `Is Bottleneck Window` to Detail, with a black border on TRUE cells.
7. **Latency time series** (`mv_route_latency_hourly`): `Hour Ts` vs `P95 Ms Ma24h`, one line per
   route, with a parameter-driven route filter. Use the 24h MA, not raw hourly p95: hourly samples
   are small and noisy.
8. **Fraud trend** (`mv_fraud_daily_region`): `Txn Date` vs `Flagged Rate Ma7`, colour by region.
   Add a second axis for `Fraud Rate Ma7` to compare model flags against confirmed fraud.
9. **Score distribution** (`mv_fraud_score_bands`): Columns `Score Band Low`, Rows `SUM(Txn Count)`
   (log axis), colour `Is Fraud`. Shows how well the score separates fraud from legitimate payments.
10. **Risk treemap** (`mv_fraud_segments`): size `SUM(Total Amount Usd)`, colour
    `SUM(Fraud Amount Usd)/SUM(Total Amount Usd)`, label `Merchant Category`.

## 4. Assemble the dashboard

Layout (1400 x 900, Automatic sizing off):

| Row | Content |
|---|---|
| Top | KPI strip (sheet 1) |
| Row 2 | Volume trend (2), spike heatmap (3) |
| Row 3 | Flow map (4), bottleneck ranking (5), latency heatmap (6) |
| Row 4 | Fraud trend (8), score distribution (9), risk treemap (10) |

Interactivity:

1. Add a **Region** quick filter (single-value dropdown), applied to all sheets that have a
   region field (Dashboard > Actions > Filter).
2. Add a **Use as Filter** action from the heatmap (3) so clicking a spike filters the fraud trend
   and flow map to that region.
3. Add a **Highlight** action from the bottleneck ranking (5) to the latency time series (7).
4. Add a Date range filter on `Txn Date` shared across the daily-grain sources
   (right-click the filter > Apply to Worksheets > Selected Worksheets).

## 5. Things the dashboard should show (use as a check on your build)

These are properties of the synthetic data, and the SQL has verified each of them:

| Story | Where to see it |
|---|---|
| Five regional volume events (APAC Feb 17, India Mar 2, Europe Apr 17, LATAM May 9, N. America Jun 12) | Spike heatmap; moving averages on the trend |
| A fraud wave with **no** volume spike (APAC, Mar 21-22) | Fraud trend vs volume trend |
| A fraud wave that coincides with a volume spike (LATAM, May 9) | Fraud trend + spike heatmap |
| One chronically slow route (GW-JNB-02) and three routes slow only at certain hours (GW-SAO-01, GW-FRA-03, GW-MUM-01) | Bottleneck ranking + latency heatmap |

## 6. Publishing

Save the workbook as `.twbx` (packaged) so the data travels with it. For Tableau Public, use the
CSV data sources. Export a PNG of the final dashboard to `reports/` and link it from the README.
