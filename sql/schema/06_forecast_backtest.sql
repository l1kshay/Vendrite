-- ============================================================================
-- Vendrite -- 06: forecast backtest scores (analytical-depth revamp, Phase B/C)
-- ----------------------------------------------------------------------------
-- ADDITIVE migration (CREATE ... IF NOT EXISTS, no DROP -- safe on a populated
-- database). Apply order: 01 -> 02 -> 03 -> 04 -> 05 -> 06.
--
-- One row per model per pipeline run: the holdout-backtest error of that model
-- (fit on all-but-the-last-horizon, scored on the held-out tail). Lets the
-- read-only dashboard show "which model did better, and by how much" without
-- re-fitting anything itself -- presentation stays separate from analytics.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analytics.forecast_backtest (
    backtest_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    generated_date  TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_version   TEXT NOT NULL,
    horizon_days    INT  NOT NULL,
    n_holdout       INT  NOT NULL,
    mae             NUMERIC(16, 2) NOT NULL,
    rmse            NUMERIC(16, 2) NOT NULL,
    mape_pct        NUMERIC(9, 2),                 -- NULL if every holdout day was 0

    CONSTRAINT uq_forecast_backtest_run_model
        UNIQUE (generated_date, model_version)
);

CREATE INDEX IF NOT EXISTS ix_forecast_backtest_generated
    ON analytics.forecast_backtest (generated_date DESC);

COMMENT ON TABLE analytics.forecast_backtest IS
    'Holdout-backtest error per model per run. generated_date groups the models compared in one run.';


-- ---- grants (idempotent) -------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON analytics.forecast_backtest TO vendrite_etl;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA analytics TO vendrite_etl;
GRANT SELECT ON analytics.forecast_backtest TO vendrite_dashboard;
