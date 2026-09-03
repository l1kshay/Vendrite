-- ============================================================================
-- Vendrite -- 03: staging schema
-- ----------------------------------------------------------------------------
-- staging.raw_transactions is the raw landing table. Every column except the
-- surrogate raw_id is TEXT and UNVALIDATED: it holds source values verbatim as
-- they arrived in the extracted CSV. Type coercion, dedupe, and star-schema
-- mapping happen downstream in etl/clean.py.
--
-- Records that fail *format/schema* validation at extract time are NOT loaded
-- here -- they are written to data/processed/quarantine_extract.csv with a
-- reason and logged. This table therefore contains only structurally-parseable
-- rows (which may still be semantically dirty).
-- ============================================================================

DROP TABLE IF EXISTS staging.raw_transactions;

CREATE TABLE staging.raw_transactions (
    raw_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- ---- source fields, stored as-is (unvalidated) --------------------------
    order_id            TEXT,
    order_datetime      TEXT,
    customer_name       TEXT,
    customer_email      TEXT,
    region              TEXT,
    signup_date         TEXT,
    product_name        TEXT,
    category            TEXT,
    unit_price          TEXT,
    quantity            TEXT,
    total_amount        TEXT,

    -- ---- ingestion provenance --------------------------------------------------
    source_file         TEXT        NOT NULL,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE staging.raw_transactions IS
    'Raw extracted transaction rows, all source columns as TEXT. ETL-write only; never queried by the dashboard.';
