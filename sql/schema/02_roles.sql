-- ============================================================================
-- Vendrite -- 02: database roles (least privilege)
-- ----------------------------------------------------------------------------
-- Creates two LOGIN roles:
--   * vendrite_etl        -> read/write on staging + analytics  (ETL & analytics jobs)
--   * vendrite_dashboard  -> read-only on analytics ONLY        (Streamlit dashboard)
--
-- SECURITY: the passwords below are PLACEHOLDERS. Immediately run
--   ALTER ROLE vendrite_etl       WITH PASSWORD '<real>';
--   ALTER ROLE vendrite_dashboard WITH PASSWORD '<real>';
-- and store the same values in .env (local) / GitHub Actions Secrets (CI).
--
-- Run as a superuser / database owner. Apply AFTER 01_schemas.sql and re-run
-- (or run 04_analytics.sql's grants) after tables exist -- the default-privilege
-- statements below also cover tables created later.
-- ============================================================================

-- ---- roles -----------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vendrite_etl') THEN
        CREATE ROLE vendrite_etl LOGIN PASSWORD 'change-me-etl';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vendrite_dashboard') THEN
        CREATE ROLE vendrite_dashboard LOGIN PASSWORD 'change-me-dashboard';
    END IF;
END
$$;

-- ---- connect + schema usage ---------------------------------------------------
GRANT CONNECT ON DATABASE vendrite TO vendrite_etl, vendrite_dashboard;

GRANT USAGE ON SCHEMA staging   TO vendrite_etl;
GRANT USAGE ON SCHEMA analytics TO vendrite_etl;

-- Dashboard role gets USAGE on analytics ONLY. It is deliberately NOT granted
-- any privilege on the `staging` schema, so it cannot read raw_transactions.
GRANT USAGE ON SCHEMA analytics TO vendrite_dashboard;

-- ---- table privileges: ETL (read/write everywhere) --------------------------
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON ALL TABLES IN SCHEMA staging   TO vendrite_etl;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON ALL TABLES IN SCHEMA analytics TO vendrite_etl;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA staging   TO vendrite_etl;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA analytics TO vendrite_etl;

-- ---- table privileges: dashboard (read-only, analytics only) ----------------
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO vendrite_dashboard;

-- ---- default privileges (apply to tables/sequences created later) -----------
-- NOTE: "created by" here means created by the current role running this file.
ALTER DEFAULT PRIVILEGES IN SCHEMA staging
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO vendrite_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO vendrite_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA staging
    GRANT USAGE, SELECT ON SEQUENCES TO vendrite_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
    GRANT USAGE, SELECT ON SEQUENCES TO vendrite_etl;

ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
    GRANT SELECT ON TABLES TO vendrite_dashboard;

-- Explicitly ensure the dashboard role can never touch staging, even if a
-- future ALTER DEFAULT PRIVILEGES is added carelessly.
REVOKE ALL ON SCHEMA staging FROM vendrite_dashboard;
