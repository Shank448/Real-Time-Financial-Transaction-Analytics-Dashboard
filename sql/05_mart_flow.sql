-- 05_mart_flow.sql
-- Origin -> destination transaction flow, weekly, with hub coordinates so Tableau can draw
-- flow lines (MAKELINE) on a map.

DROP MATERIALIZED VIEW IF EXISTS mv_region_flow_weekly CASCADE;

CREATE MATERIALIZED VIEW mv_region_flow_weekly AS
SELECT date_trunc('week', t.txn_date)::date                      AS week_start,
       t.origin_region, t.dest_region, t.is_cross_border,
       o.hub_lat AS origin_lat, o.hub_lon AS origin_lon,
       d.hub_lat AS dest_lat,   d.hub_lon AS dest_lon,
       COUNT(*)                                                  AS txn_count,
       ROUND(SUM(t.amount_usd), 2)                               AS total_amount_usd,
       ROUND(AVG(t.latency_ms)::numeric, 1)                      AS avg_latency_ms,
       ROUND(AVG((t.status = 'SUCCESS')::int::numeric), 4)       AS success_rate,
       ROUND(AVG(t.is_fraud::int::numeric), 5)                   AS fraud_rate
FROM transactions_clean t
JOIN dim_region o ON o.region = t.origin_region
JOIN dim_region d ON d.region = t.dest_region
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8;
