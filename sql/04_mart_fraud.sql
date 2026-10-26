-- 04_mart_fraud.sql
-- Fraud-probability metrics. `fraud_probability` is the model score (0-1);
-- a transaction is FLAGGED for review when score >= FRAUD_THRESHOLD.
-- `is_fraud` is the confirmed label, so we can measure flag precision / recall too.

DROP MATERIALIZED VIEW IF EXISTS mv_fraud_daily_region CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_fraud_score_bands CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_fraud_segments CASCADE;

CREATE MATERIALIZED VIEW mv_fraud_daily_region AS
WITH d AS (
    SELECT txn_date, origin_region,
           COUNT(*)                                                           AS txn_count,
           AVG(fraud_probability)                                             AS avg_fraud_prob,
           COUNT(*) FILTER (WHERE fraud_probability >= {{FRAUD_THRESHOLD}})   AS flagged_count,
           COUNT(*) FILTER (WHERE is_fraud)                                   AS fraud_label_count,
           COUNT(*) FILTER (WHERE is_fraud AND fraud_probability >= {{FRAUD_THRESHOLD}}) AS true_pos,
           COALESCE(SUM(amount_usd) FILTER (WHERE fraud_probability >= {{FRAUD_THRESHOLD}}), 0) AS flagged_amount_usd,
           COALESCE(SUM(amount_usd) FILTER (WHERE is_fraud), 0)               AS fraud_amount_usd
    FROM transactions_clean
    GROUP BY txn_date, origin_region
)
SELECT txn_date, origin_region, txn_count,
       ROUND(avg_fraud_prob::numeric, 5)                                            AS avg_fraud_prob,
       flagged_count, fraud_label_count, true_pos,
       ROUND(flagged_amount_usd, 2)                                                 AS flagged_amount_usd,
       ROUND(fraud_amount_usd, 2)                                                   AS fraud_amount_usd,
       ROUND(flagged_count::numeric / txn_count, 5)                                 AS flagged_rate,
       ROUND(fraud_label_count::numeric / txn_count, 5)                             AS fraud_rate,
       ROUND(true_pos::numeric / NULLIF(flagged_count, 0), 4)                       AS flag_precision,
       ROUND(true_pos::numeric / NULLIF(fraud_label_count, 0), 4)                   AS flag_recall,
       ROUND((SUM(flagged_count) OVER w7)::numeric / NULLIF(SUM(txn_count) OVER w7, 0), 5) AS flagged_rate_ma7,
       ROUND((SUM(fraud_label_count) OVER w7)::numeric / NULLIF(SUM(txn_count) OVER w7, 0), 5) AS fraud_rate_ma7
FROM d
WINDOW w7 AS (PARTITION BY origin_region ORDER BY txn_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW);

-- Score distribution (for the histogram): 10 bands x region x confirmed label
CREATE MATERIALIZED VIEW mv_fraud_score_bands AS
SELECT origin_region,
       LEAST(FLOOR(fraud_probability * 10), 9)::int / 10.0   AS score_band_low,
       is_fraud,
       COUNT(*)                                              AS txn_count
FROM transactions_clean
WHERE fraud_probability IS NOT NULL
GROUP BY 1, 2, 3;

-- Where is risk concentrated?  region x category x payment method
CREATE MATERIALIZED VIEW mv_fraud_segments AS
SELECT origin_region, merchant_category, payment_method,
       COUNT(*)                                                                AS txn_count,
       ROUND(SUM(amount_usd), 2)                                               AS total_amount_usd,
       ROUND(AVG(fraud_probability)::numeric, 5)                               AS avg_fraud_prob,
       ROUND(AVG((fraud_probability >= {{FRAUD_THRESHOLD}})::int::numeric), 5) AS flagged_rate,
       ROUND(AVG(is_fraud::int::numeric), 5)                                   AS fraud_rate,
       ROUND(COALESCE(SUM(amount_usd) FILTER (WHERE is_fraud), 0), 2)          AS fraud_amount_usd
FROM transactions_clean
GROUP BY 1, 2, 3;
