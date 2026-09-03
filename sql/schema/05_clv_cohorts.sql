-- ============================================================================
-- Vendrite -- 05: CLV + cohort retention (analytical-depth revamp, Phase A)
-- ----------------------------------------------------------------------------
-- ADDITIVE migration. Unlike 03/04 this file has NO DROP statements and uses
-- CREATE ... IF NOT EXISTS everywhere, so it is safe to apply to a database
-- that already holds data. Apply order: 01 -> 02 -> 03 -> 04 -> 05.
--
-- Assumptions where the revamp spec was ambiguous (stated, not guessed):
--   * customer_clv is a NEW append-only history table (a sibling of
--     customer_segments), NOT a column bolted onto customer_segments -- this
--     leaves the existing table and its consumers untouched and lets us store
--     every CLV component, so the final number is auditable.
--   * cohort_month is a DATE pinned to the first day of the signup month.
--   * both tables get a surrogate *_id PK and a computed_date, matching the
--     pattern already used by customer_segments / sales_forecast.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Customer Lifetime Value -- append-only history, one row per customer per run
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analytics.customer_clv (
    clv_id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id           INT  NOT NULL,
    computed_date         DATE NOT NULL,
    avg_order_value       NUMERIC(14, 2) NOT NULL,   -- customer's own mean order value
    purchase_freq_annual  NUMERIC(12, 4) NOT NULL,   -- orders per year over observed tenure
    avg_lifespan_years    NUMERIC(8, 3)  NOT NULL,   -- global 1 / churn_rate, clamped
    gross_margin          NUMERIC(6, 4)  NOT NULL,   -- margin assumption in effect for this run
    predicted_clv         NUMERIC(14, 2) NOT NULL,   -- AOV * freq * lifespan * margin
    method_version        TEXT NOT NULL,

    CONSTRAINT fk_customer_clv_customer
        FOREIGN KEY (customer_id) REFERENCES analytics.dim_customer (customer_id)
);

CREATE INDEX IF NOT EXISTS ix_customer_clv_customer_computed
    ON analytics.customer_clv (customer_id, computed_date);

COMMENT ON TABLE analytics.customer_clv IS
    'Heuristic CLV history. One row appended per customer per computation cycle; the components are stored so the figure can be re-derived by hand.';


-- ---------------------------------------------------------------------------
-- Cohort retention -- signup-month cohorts x months-since-signup grid
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analytics.cohort_retention (
    cohort_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    computed_date         DATE NOT NULL,
    cohort_month          DATE NOT NULL,             -- first day of the signup month
    months_since_signup   INT  NOT NULL CHECK (months_since_signup >= 0),
    cohort_size           INT  NOT NULL CHECK (cohort_size > 0),
    retained_customers    INT  NOT NULL CHECK (retained_customers >= 0),
    retention_rate        NUMERIC(6, 4) NOT NULL,    -- retained_customers / cohort_size

    CONSTRAINT uq_cohort_retention_cell
        UNIQUE (computed_date, cohort_month, months_since_signup)
);

CREATE INDEX IF NOT EXISTS ix_cohort_retention_month
    ON analytics.cohort_retention (cohort_month, months_since_signup);

COMMENT ON TABLE analytics.cohort_retention IS
    'Signup-month cohort retention grid. retention_rate = retained_customers / cohort_size for each (cohort_month, months_since_signup). Only data-observable cells are stored.';


-- ---------------------------------------------------------------------------
-- Grants (idempotent) -- same split as 02/04: ETL writes, dashboard reads.
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON analytics.customer_clv, analytics.cohort_retention TO vendrite_etl;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA analytics TO vendrite_etl;
GRANT SELECT ON analytics.customer_clv, analytics.cohort_retention TO vendrite_dashboard;
