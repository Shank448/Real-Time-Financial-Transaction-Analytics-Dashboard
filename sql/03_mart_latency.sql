-- 03_mart_latency.sql
-- Find high-latency network routing bottlenecks.
-- Method: compare each route's p95 latency to a PEER BASELINE = the median of all routes' p95.
-- A route / time-window is a bottleneck when p95 > LATENCY_BREACH_MULT x peer baseline.
--   * Percentiles (not means): tail latency is what actually breaks payment SLAs.
--   * Median-of-routes baseline (not the global p95): the global p95 is dragged upward by the
--     very bottleneck traffic we are trying to detect, which hides moderate bottlenecks.

DROP MATERIALIZED VIEW IF EXISTS mv_route_latency_summary CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_route_latency_hod CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_route_latency_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_global_latency CASCADE;

CREATE MATERIALIZED VIEW mv_global_latency AS
WITH route_p95 AS (
    SELECT route_id, percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
    FROM transactions_clean
    WHERE latency_ms IS NOT NULL AND route_id <> 'UNKNOWN'
    GROUP BY route_id
)
SELECT COUNT(*)                                                    AS measured_count,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)    AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)    AS p95_ms,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)    AS p99_ms,
       (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY p95_ms) FROM route_p95) AS peer_p95_ms
FROM transactions_clean
WHERE latency_ms IS NOT NULL AND route_id <> 'UNKNOWN';

-- Hourly time series per route + trailing-24h moving average of p95
CREATE MATERIALIZED VIEW mv_route_latency_hourly AS
WITH hourly AS (
    SELECT route_id,
           date_trunc('hour', event_ts)                                   AS hour_ts,
           COUNT(*)                                                       AS txn_count,
           COUNT(latency_ms)                                              AS measured_count,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)       AS p50_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)       AS p95_ms,
           AVG((status = 'FAILED')::int::numeric)                         AS failure_rate
    FROM transactions_clean
    WHERE route_id <> 'UNKNOWN' AND latency_ms IS NOT NULL
    GROUP BY route_id, date_trunc('hour', event_ts)
)
SELECT h.route_id, h.hour_ts, h.txn_count, h.measured_count,
       ROUND(h.p50_ms::numeric, 1) AS p50_ms,
       ROUND(h.p95_ms::numeric, 1) AS p95_ms,
       ROUND(AVG(h.p95_ms) OVER (PARTITION BY h.route_id ORDER BY h.hour_ts
                                 RANGE BETWEEN INTERVAL '23 hours' PRECEDING AND CURRENT ROW)::numeric, 1)
                                   AS p95_ms_ma24h,
       ROUND(h.failure_rate, 4)    AS failure_rate,
       (h.measured_count >= {{MIN_HOURLY_N}}
        AND h.p95_ms > {{LATENCY_BREACH_MULT}} * g.peer_p95_ms)          AS is_breach
FROM hourly h CROSS JOIN mv_global_latency g;

-- Route x UTC hour-of-day (stable sample sizes -> reliable bottleneck windows)
CREATE MATERIALIZED VIEW mv_route_latency_hod AS
WITH hod AS (
    SELECT t.route_id,
           EXTRACT(HOUR FROM t.event_ts AT TIME ZONE 'UTC')::int                  AS hour_utc,
           COUNT(t.latency_ms)                                                    AS measured_count,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY t.latency_ms)             AS p50_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY t.latency_ms)             AS p95_ms,
           AVG((t.status = 'FAILED')::int::numeric)                               AS failure_rate
    FROM transactions_clean t
    WHERE t.latency_ms IS NOT NULL AND t.route_id <> 'UNKNOWN'
    GROUP BY t.route_id, EXTRACT(HOUR FROM t.event_ts AT TIME ZONE 'UTC')
)
SELECT h.route_id, d.gateway_city, d.region, h.hour_utc, h.measured_count,
       ROUND(h.p50_ms::numeric, 1)                          AS p50_ms,
       ROUND(h.p95_ms::numeric, 1)                          AS p95_ms,
       ROUND((h.p95_ms / g.peer_p95_ms)::numeric, 2)        AS p95_vs_peer,
       ROUND(h.failure_rate, 4)                             AS failure_rate,
       (h.p95_ms > {{LATENCY_BREACH_MULT}} * g.peer_p95_ms) AS is_bottleneck_window
FROM hod h
JOIN dim_route d USING (route_id)
CROSS JOIN mv_global_latency g;

-- One row per route: classify and rank
--   CHRONIC     : breaches the threshold in at least half of the 24 hour-of-day windows
--   PEAK-WINDOW : breaches in 1-11 hour windows (congestion at specific times of day)
--   HEALTHY     : never breaches
CREATE MATERIALIZED VIEW mv_route_latency_summary AS
WITH overall AS (
    SELECT route_id,
           COUNT(latency_ms)                                          AS measured_count,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)   AS p50_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)   AS p95_ms,
           percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)   AS p99_ms,
           AVG((status = 'FAILED')::int::numeric)                     AS failure_rate
    FROM transactions_clean
    WHERE latency_ms IS NOT NULL AND route_id <> 'UNKNOWN'
    GROUP BY route_id
),
windows AS (
    SELECT route_id,
           COUNT(*) FILTER (WHERE is_bottleneck_window)               AS breach_hours_of_day,
           MAX(p95_ms)                                                AS worst_hour_p95_ms,
           (ARRAY_AGG(hour_utc ORDER BY p95_ms DESC))[1]              AS worst_hour_utc
    FROM mv_route_latency_hod
    GROUP BY route_id
)
SELECT o.route_id, d.gateway_city, d.region,
       o.measured_count,
       ROUND(o.p50_ms::numeric, 1)                    AS p50_ms,
       ROUND(o.p95_ms::numeric, 1)                    AS p95_ms,
       ROUND(o.p99_ms::numeric, 1)                    AS p99_ms,
       ROUND((o.p95_ms / g.peer_p95_ms)::numeric, 2)  AS p95_vs_peer,
       ROUND(o.failure_rate, 4)                       AS failure_rate,
       w.breach_hours_of_day,
       w.worst_hour_utc,
       w.worst_hour_p95_ms,
       CASE WHEN w.breach_hours_of_day >= 12 THEN 'CHRONIC'
            WHEN w.breach_hours_of_day >= 1  THEN 'PEAK-WINDOW'
            ELSE 'HEALTHY' END                        AS bottleneck_type,
       RANK() OVER (ORDER BY o.p95_ms DESC)           AS p95_rank
FROM overall o
JOIN windows w USING (route_id)
JOIN dim_route d USING (route_id)
CROSS JOIN mv_global_latency g;
