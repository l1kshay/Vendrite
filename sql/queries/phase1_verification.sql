-- ============================================================================
-- Vendrite -- Phase 1 verification queries
-- ----------------------------------------------------------------------------
-- Run these directly (psql / pgAdmin) AFTER `python -m etl.run_etl --generate`
-- to confirm staging and analytics tables are populated correctly.
-- Expected results are described in comments; exact counts depend on the
-- VENDRITE_MOCK_* settings and the random seed.
-- ============================================================================

-- 1. Staging landed rows (structurally-parseable extract rows).
SELECT count(*) AS staging_rows FROM staging.raw_transactions;

-- 2. Dimension row counts (should be > 0 and <= the mock N_* settings).
SELECT
    (SELECT count(*) FROM analytics.dim_customer) AS customers,
    (SELECT count(*) FROM analytics.dim_product)  AS products,
    (SELECT count(*) FROM analytics.dim_date)     AS dates;

-- 3. Fact rows + referential sanity: every FK must resolve (expect 0 orphans).
SELECT count(*) AS fact_rows FROM analytics.fact_sales;

SELECT count(*) AS orphan_customer_fk
FROM analytics.fact_sales f
LEFT JOIN analytics.dim_customer d ON d.customer_id = f.customer_id
WHERE d.customer_id IS NULL;

SELECT count(*) AS orphan_product_fk
FROM analytics.fact_sales f
LEFT JOIN analytics.dim_product d ON d.product_id = f.product_id
WHERE d.product_id IS NULL;

SELECT count(*) AS orphan_date_fk
FROM analytics.fact_sales f
LEFT JOIN analytics.dim_date d ON d.date_id = f.date_id
WHERE d.date_id IS NULL;

-- 4. Data-quality invariants that cleaning must guarantee (all expect 0).
SELECT count(*) AS bad_quantity     FROM analytics.fact_sales WHERE quantity <= 0;
SELECT count(*) AS bad_total_amount FROM analytics.fact_sales WHERE total_amount <= 0;
SELECT count(*) AS null_email       FROM analytics.dim_customer WHERE email IS NULL;
SELECT count(*) AS dup_email
FROM (SELECT email FROM analytics.dim_customer GROUP BY email HAVING count(*) > 1) t;

-- 5. Date span of the loaded facts (should cover ~VENDRITE_MOCK_MONTHS_BACK months).
SELECT min(d.date) AS first_sale, max(d.date) AS last_sale
FROM analytics.fact_sales f
JOIN analytics.dim_date d ON d.date_id = f.date_id;

-- 6. Latest ETL run outcomes.
SELECT run_id, run_timestamp, status, records_processed, error_message
FROM analytics.etl_run_log
ORDER BY run_id DESC
LIMIT 10;

-- 7. Top 5 categories by revenue -- eyeball check that numbers look plausible.
SELECT p.category, round(sum(f.total_amount), 2) AS revenue, sum(f.quantity) AS units
FROM analytics.fact_sales f
JOIN analytics.dim_product p ON p.product_id = f.product_id
GROUP BY p.category
ORDER BY revenue DESC
LIMIT 5;
