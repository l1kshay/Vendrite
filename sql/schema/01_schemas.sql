-- ============================================================================
-- Vendrite -- 01: schemas
-- ----------------------------------------------------------------------------
-- Apply order: 01_schemas -> 02_roles -> 03_staging -> 04_analytics
-- Run as a superuser / database owner against the (empty) `vendrite` database.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS analytics;

COMMENT ON SCHEMA staging   IS 'Raw, unvalidated landing zone. Written by ETL only.';
COMMENT ON SCHEMA analytics IS 'Cleaned star-schema warehouse. The ONLY schema the dashboard/analytics code may read.';
