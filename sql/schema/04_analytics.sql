-- ============================================================================
-- Vendrite -- 04: analytics schema (star schema + operational tables)
-- ----------------------------------------------------------------------------
-- Apply AFTER 01_schemas.sql, 02_roles.sql, 03_staging.sql.
--
-- Assumptions where the spec was ambiguous (stated, not silently guessed):
--   * dim_date.day  = day-of-month (1-31), INT.
--   * dim_date.date_id encodes the date as YYYYMMDD (e.g. 2026-08-31 -> 20260831).
--   * dim_product gets a UNIQUE(name, category) natural key so the ETL can do an
--     idempotent upsert. (Not listed in the spec; required for re-runnable loads.)
--   * customer_segments / sales_forecast get a surrogate *_id PRIMARY KEY in
--     addition to the spec's columns, because both are append-only history
--     tables and need a stable row identifier. The spec's semantic keys
--     (customer_id FK; model_version) are preserved exactly.
--   * fact_sales gets UNIQUE(order_id, product_id) so re-running the ETL does
--     not create duplicate sale rows. (Not listed; required for idempotency.)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Dimensions
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS analytics.fact_sales        CASCADE;
DROP TABLE IF EXISTS analytics.customer_segments CASCADE;
DROP TABLE IF EXISTS analytics.sales_forecast    CASCADE;
DROP TABLE IF EXISTS analytics.dim_customer      CASCADE;
DROP TABLE IF EXISTS analytics.dim_product       CASCADE;
DROP TABLE IF EXISTS analytics.dim_date          CASCADE;
DROP TABLE IF EXISTS analytics.etl_run_log       CASCADE;


CREATE TABLE analytics.dim_customer (
    customer_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    signup_date   DATE,
    region        TEXT,
    CONSTRAINT uq_dim_customer_email UNIQUE (email)   -- spec: UNIQUE on email
);
COMMENT ON TABLE analytics.dim_customer IS 'Customer dimension. Natural key = email (UNIQUE).';


CREATE TABLE analytics.dim_product (
    product_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    unit_price    NUMERIC(12, 2),
    -- ASSUMPTION (idempotent ETL upsert target); not in original spec:
    CONSTRAINT uq_dim_product_name_category UNIQUE (name, category)
);
COMMENT ON TABLE analytics.dim_product IS 'Product dimension. Natural key = (name, category).';


CREATE TABLE analytics.dim_date (
    date_id       INT  PRIMARY KEY,                  -- YYYYMMDD
    date          DATE NOT NULL,
    day           INT  NOT NULL,                     -- day-of-month 1..31
    month         INT  NOT NULL,                     -- 1..12
    quarter       INT  NOT NULL,                     -- 1..4
    year          INT  NOT NULL,
    is_weekend    BOOLEAN NOT NULL,
    CONSTRAINT uq_dim_date_date UNIQUE (date)
);
COMMENT ON TABLE analytics.dim_date IS 'Calendar dimension, one row per date present in fact_sales.';


-- ---------------------------------------------------------------------------
-- Fact
-- ---------------------------------------------------------------------------
CREATE TABLE analytics.fact_sales (
    sale_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id   INT NOT NULL,                      -- spec: NOT NULL FK
    product_id    INT NOT NULL,                      -- spec: NOT NULL FK
    date_id       INT NOT NULL,                      -- spec: NOT NULL FK
    order_id      TEXT NOT NULL,
    quantity      INT  NOT NULL CHECK (quantity > 0),
    total_amount  NUMERIC(14, 2) NOT NULL,           -- spec: NOT NULL

    CONSTRAINT fk_fact_sales_customer
        FOREIGN KEY (customer_id) REFERENCES analytics.dim_customer (customer_id),
    CONSTRAINT fk_fact_sales_product
        FOREIGN KEY (product_id)  REFERENCES analytics.dim_product (product_id),
    CONSTRAINT fk_fact_sales_date
        FOREIGN KEY (date_id)     REFERENCES analytics.dim_date (date_id),

    -- ASSUMPTION (idempotent ETL): one fact row per order line.
    CONSTRAINT uq_fact_sales_orderline UNIQUE (order_id, product_id)
);
COMMENT ON TABLE analytics.fact_sales IS 'Sales fact grain = one order line (order_id x product_id).';

-- spec: composite index on fact_sales(date_id, customer_id)
CREATE INDEX ix_fact_sales_date_customer
    ON analytics.fact_sales (date_id, customer_id);


-- ---------------------------------------------------------------------------
-- Customer segments -- append-only history (one row per computation cycle)
-- ---------------------------------------------------------------------------
CREATE TABLE analytics.customer_segments (
    segment_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,  -- ASSUMPTION: surrogate row id
    customer_id     INT NOT NULL,
    recency         INT,               -- days since most recent purchase
    frequency       INT,               -- distinct orders in the analysis window
    monetary        NUMERIC(14, 2),    -- total spend in the analysis window
    segment_label   TEXT NOT NULL,     -- e.g. 'Champion', 'Loyal', 'At Risk', 'New'
    computed_date   DATE NOT NULL,

    CONSTRAINT fk_customer_segments_customer
        FOREIGN KEY (customer_id) REFERENCES analytics.dim_customer (customer_id)
);
COMMENT ON TABLE analytics.customer_segments IS
    'RFM segmentation history. INSERT a new row set per run; never overwrite -- preserves segment history.';

-- spec: composite index on customer_segments(customer_id, computed_date)
CREATE INDEX ix_customer_segments_customer_computed
    ON analytics.customer_segments (customer_id, computed_date);


-- ---------------------------------------------------------------------------
-- Sales forecast -- standalone, NOT joined to fact_sales
-- ---------------------------------------------------------------------------
CREATE TABLE analytics.sales_forecast (
    forecast_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,  -- ASSUMPTION: surrogate row id
    forecast_date   DATE NOT NULL,        -- the future date being predicted
    predicted_sales NUMERIC(14, 2) NOT NULL,
    model_version   TEXT NOT NULL,        -- e.g. 'linreg-v1'
    generated_date  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_sales_forecast_version_date UNIQUE (model_version, forecast_date, generated_date)
);
COMMENT ON TABLE analytics.sales_forecast IS
    'Versioned demand forecast. Deliberately has no FK to fact_sales so forecast versioning is independent.';


-- ---------------------------------------------------------------------------
-- ETL run log
-- ---------------------------------------------------------------------------
CREATE TABLE analytics.etl_run_log (
    run_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_timestamp     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status            TEXT NOT NULL CHECK (status IN ('STARTED', 'SUCCESS', 'FAILED')),
    records_processed INT,
    error_message     TEXT
);
COMMENT ON TABLE analytics.etl_run_log IS 'One row per pipeline stage/run: STARTED then SUCCESS or FAILED.';


-- ---------------------------------------------------------------------------
-- Re-apply grants now that the tables exist (02_roles.sql also sets defaults).
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON ALL TABLES IN SCHEMA analytics TO vendrite_etl;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA analytics TO vendrite_etl;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO vendrite_dashboard;
