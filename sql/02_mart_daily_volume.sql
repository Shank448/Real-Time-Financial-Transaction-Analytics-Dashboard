-- 02_mart_daily_volume.sql
-- Daily transaction volume per region with gap-filled calendar, rolling moving averages
-- and statistical spike detection.
--   * 7-day / 30-day moving averages (ROWS frame over a gap-free calendar => exact windows)
--   * spike  = today's volume is > SPIKE_Z std-devs above the trailing 28-day baseline
--              (baseline EXCLUDES today, so a spike cannot hide itself)

DROP MATERIALIZED VIEW IF EXISTS mv_daily_total_volume CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_daily_region_volume CASCADE;

CREATE MATERIALIZED VIEW mv_daily_region_volume AS
WITH calendar AS (
    SELECT d::date AS txn_date
    FROM generate_series((SELECT MIN(txn_date) FROM transactions_clean),
                         (SELECT MAX(txn_date) FROM transactions_clean),
                         INTERVAL '1 day') AS d
),
regions AS (
    SELECT DISTINCT origin_region FROM transactions_clean
),
daily AS (
    SELECT txn_date, origin_region,
           COUNT(*)                                   AS txn_count,
           SUM(amount_usd)                            AS total_amount_usd,
           AVG(amount_usd)                            AS avg_amount_usd,
           AVG((status = 'SUCCESS')::int::numeric)    AS success_rate
    FROM transactions_clean
    GROUP BY txn_date, origin_region
),
grid AS (                                             -- one row per (day, region), zeros filled
    SELECT c.txn_date, r.origin_region,
           COALESCE(d.txn_count, 0)        AS txn_count,
           COALESCE(d.total_amount_usd, 0) AS total_amount_usd,
           d.avg_amount_usd, d.success_rate
    FROM calendar c
    CROSS JOIN regions r
    LEFT JOIN daily d USING (txn_date, origin_region)
),
rolling AS (
    SELECT g.*,
           CASE WHEN COUNT(*) OVER w7  = 7  THEN AVG(txn_count)        OVER w7  END AS txn_count_ma7,
           CASE WHEN COUNT(*) OVER w30 = 30 THEN AVG(txn_count)        OVER w30 END AS txn_count_ma30,
           CASE WHEN COUNT(*) OVER w7  = 7  THEN AVG(total_amount_usd) OVER w7  END AS amount_usd_ma7,
           CASE WHEN COUNT(*) OVER wb >= 14 THEN AVG(txn_count)         OVER wb END AS baseline_mean,
           CASE WHEN COUNT(*) OVER wb >= 14 THEN STDDEV_SAMP(txn_count) OVER wb END AS baseline_std
    FROM grid g
    WINDOW w7  AS (PARTITION BY origin_region ORDER BY txn_date ROWS BETWEEN 6  PRECEDING AND CURRENT ROW),
           w30 AS (PARTITION BY origin_region ORDER BY txn_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW),
           wb  AS (PARTITION BY origin_region ORDER BY txn_date ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING)
)
SELECT txn_date,
       origin_region,
       txn_count,
       ROUND(total_amount_usd, 2)                                   AS total_amount_usd,
       ROUND(avg_amount_usd, 2)                                     AS avg_amount_usd,
       ROUND(success_rate, 4)                                       AS success_rate,
       ROUND(txn_count_ma7, 1)                                      AS txn_count_ma7,
       ROUND(txn_count_ma30, 1)                                     AS txn_count_ma30,
       ROUND(amount_usd_ma7, 2)                                     AS amount_usd_ma7,
       ROUND(((txn_count - baseline_mean) / NULLIF(baseline_std, 0))::numeric, 2) AS volume_zscore,
       COALESCE((txn_count - baseline_mean) / NULLIF(baseline_std, 0) > {{SPIKE_Z}}, FALSE) AS is_spike
FROM rolling;

CREATE UNIQUE INDEX ON mv_daily_region_volume (txn_date, origin_region);

-- All-region daily totals + moving averages (for the headline trend chart)
CREATE MATERIALIZED VIEW mv_daily_total_volume AS
WITH daily AS (
    SELECT txn_date, SUM(txn_count) AS txn_count, SUM(total_amount_usd) AS total_amount_usd
    FROM mv_daily_region_volume
    GROUP BY txn_date
)
SELECT txn_date,
       txn_count,
       total_amount_usd,
       ROUND(CASE WHEN COUNT(*) OVER w7  = 7  THEN AVG(txn_count) OVER w7  END, 1) AS txn_count_ma7,
       ROUND(CASE WHEN COUNT(*) OVER w30 = 30 THEN AVG(txn_count) OVER w30 END, 1) AS txn_count_ma30
FROM daily
WINDOW w7  AS (ORDER BY txn_date ROWS BETWEEN 6  PRECEDING AND CURRENT ROW),
       w30 AS (ORDER BY txn_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW);
