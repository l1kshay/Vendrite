"""
Short-term sales forecasting.

Reads ONLY from the ``analytics`` schema, aggregates ``fact_sales`` to a
gap-free daily revenue series, and fits **two deliberately different models**,
writing each one's next ``VENDRITE_FORECAST_HORIZON_DAYS`` days to
``analytics.sales_forecast`` tagged with its own ``model_version`` (the table
is standalone -- no FK to fact_sales -- so versions stay independent). A
holdout backtest scores both so the dashboard can say *which* did better and,
more importantly, *why*.

Model 1 -- ``linreg-v1`` : explainable OLS linear regression
------------------------------------------------------------
    revenue(day) ~ b0 + b_t * t + sum_k b_dow_k * [weekday == k]

* ``t``        = integer day offset from the first observed day -> ``b_t`` is
                the average revenue trend per day, readable straight off the
                coefficient.
* ``dow_1..6`` = one-hot weekday flags (Monday = baseline).

Fully transparent, no hidden state. Extrapolates a *straight* trend for ever
and the weekly shape is fixed. Best when the trend really is linear and the
model has to be explained to a stakeholder.

Model 2 -- ``holtwinters-v1`` : Holt-Winters triple exponential smoothing
-----------------------------------------------------------------------
Additive level + additive trend + additive 7-day seasonal component, each
updated with exponentially-decaying weights (alpha / beta / gamma, optimised
by statsmodels). Adapts to a *recent* change in level or trend instead of
being dominated by old data, and the weekly profile can drift. Best when the
level / trend / season evolve over time. Less transparent (no single trend
coefficient), needs >= 2 full seasonal cycles, and can chase noise if the
smoothing weights come out high.

Both models clip predictions at 0 (negative sales are not meaningful).

Pure functions (``build_daily_series``, ``make_features``, ``fit_model``,
``forecast``, ``fit_holtwinters``, ``forecast_holtwinters``, ``model_errors``,
``backtest``) are unit-tested.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from statsmodels.tsa.holtwinters import ExponentialSmoothing

# --- ensure the project root is importable, however this file is launched ----
# `python -m ...` and pytest put the repo root on sys.path; a bare
# `python analytics/forecasting.py` does not. Add it before first-party imports.
import sys as _sys
from pathlib import Path as _Path

_ROOT = str(_Path(__file__).resolve().parents[1])
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
# ---------------------------------------------------------------------------

from config import settings
from etl.load import get_engine, log_run, reflect

logger = logging.getLogger(__name__)

_DOW_FEATURES = [f"dow_{k}" for k in range(1, 7)]
FEATURE_COLUMNS = ["t", *_DOW_FEATURES]


# ---------------------------------------------------------------------------
# read (analytics schema only)
# ---------------------------------------------------------------------------
def read_daily_revenue(engine: Engine) -> pd.DataFrame:
    """Return columns [date, revenue] -- one row per date present in fact_sales."""
    md = reflect(engine)
    fact = md.tables[f"{settings.ANALYTICS_SCHEMA}.fact_sales"]
    dim_date = md.tables[f"{settings.ANALYTICS_SCHEMA}.dim_date"]
    stmt = (
        select(
            dim_date.c.date.label("date"),
            func.sum(fact.c.total_amount).label("revenue"),
        )
        .select_from(fact.join(dim_date, dim_date.c.date_id == fact.c.date_id))
        .group_by(dim_date.c.date)
        .order_by(dim_date.c.date)
    )
    with engine.connect() as conn:
        df = pd.read_sql(stmt, conn)
    df["date"] = pd.to_datetime(df["date"])
    df["revenue"] = pd.to_numeric(df["revenue"])
    logger.info("Read %d daily revenue points", len(df))
    return df


# ---------------------------------------------------------------------------
# shared: daily series
# ---------------------------------------------------------------------------
def build_daily_series(daily: pd.DataFrame) -> pd.DataFrame:
    """Reindex to a gap-free daily range; missing days -> 0 revenue."""
    if daily.empty:
        return daily.assign(date=pd.to_datetime([]), revenue=[])
    full = pd.date_range(daily["date"].min(), daily["date"].max(), freq="D")
    out = (
        daily.set_index("date")
        .reindex(full)
        .rename_axis("date")
        .reset_index()
    )
    out["revenue"] = out["revenue"].fillna(0.0)
    return out


# ---------------------------------------------------------------------------
# model 1 -- linear regression (explainable)
# ---------------------------------------------------------------------------
def make_features(dates: pd.DatetimeIndex, origin: pd.Timestamp) -> pd.DataFrame:
    """Design matrix: integer day offset + one-hot weekday (Mon baseline)."""
    dates = pd.DatetimeIndex(dates)
    feats = pd.DataFrame(index=range(len(dates)))
    feats["t"] = (dates - origin).days.to_numpy()
    weekday = dates.weekday.to_numpy()
    for k in range(1, 7):
        feats[f"dow_{k}"] = (weekday == k).astype(int)
    return feats[FEATURE_COLUMNS]


def fit_model(series: pd.DataFrame) -> tuple[LinearRegression, pd.Timestamp]:
    """Fit OLS on the daily series. Returns (model, origin date)."""
    origin = series["date"].min()
    X = make_features(pd.DatetimeIndex(series["date"]), origin)
    y = series["revenue"].to_numpy()
    model = LinearRegression().fit(X, y)
    return model, origin


def forecast(
    model: LinearRegression,
    origin: pd.Timestamp,
    last_date: pd.Timestamp,
    horizon_days: int,
) -> pd.DataFrame:
    """Predict revenue for the ``horizon_days`` days after ``last_date``."""
    future = pd.date_range(last_date + pd.Timedelta(days=1), periods=horizon_days, freq="D")
    X = make_features(future, origin)
    preds = model.predict(X)
    preds = np.clip(preds, a_min=0.0, a_max=None).round(2)
    return pd.DataFrame({"forecast_date": future.date, "predicted_sales": preds})


# ---------------------------------------------------------------------------
# model 2 -- Holt-Winters exponential smoothing (seasonality-aware)
# ---------------------------------------------------------------------------
def fit_holtwinters(series: pd.DataFrame, *, seasonal_periods: int):
    """Fit additive level+trend+seasonal exponential smoothing.

    Raises ``ValueError`` if there are fewer than two full seasonal cycles --
    Holt-Winters cannot initialise a season it has not seen twice.
    """
    need = 2 * seasonal_periods
    if len(series) < need:
        raise ValueError(
            f"Holt-Winters needs >= {need} days of history, got {len(series)}"
        )
    y = pd.Series(
        series["revenue"].to_numpy(dtype="float64"),
        index=pd.DatetimeIndex(series["date"]),
    )
    y.index.freq = "D"
    return ExponentialSmoothing(
        y,
        trend="add",
        seasonal="add",
        seasonal_periods=seasonal_periods,
        initialization_method="estimated",
    ).fit()


def forecast_holtwinters(model, last_date: pd.Timestamp, horizon_days: int) -> pd.DataFrame:
    """Predict revenue for the ``horizon_days`` days after ``last_date``."""
    future = pd.date_range(last_date + pd.Timedelta(days=1), periods=horizon_days, freq="D")
    preds = np.asarray(model.forecast(horizon_days), dtype="float64")
    preds = np.clip(preds, a_min=0.0, a_max=None).round(2)
    return pd.DataFrame({"forecast_date": future.date, "predicted_sales": preds})


def hw_smoothing_params(model) -> dict[str, float]:
    """The three fitted smoothing weights, rounded -- for logging / display."""
    p = model.params
    return {
        "alpha_level": round(float(p.get("smoothing_level", float("nan"))), 4),
        "beta_trend": round(float(p.get("smoothing_trend", float("nan"))), 4),
        "gamma_season": round(float(p.get("smoothing_seasonal", float("nan"))), 4),
    }


# ---------------------------------------------------------------------------
# model comparison -- holdout backtest
# ---------------------------------------------------------------------------
def model_errors(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    """MAE, RMSE and MAPE (%) between two equal-length vectors. MAPE skips
    zero-actual days (division would be undefined)."""
    actual = np.asarray(actual, dtype="float64")
    predicted = np.asarray(predicted, dtype="float64")
    err = predicted - actual
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    nz = actual != 0.0
    mape = float(np.mean(np.abs(err[nz] / actual[nz])) * 100.0) if nz.any() else float("nan")
    return {"mae": round(mae, 2), "rmse": round(rmse, 2), "mape_pct": round(mape, 2)}


def backtest(
    series: pd.DataFrame,
    *,
    horizon_days: int,
    seasonal_periods: int,
) -> pd.DataFrame:
    """Hold out the last ``horizon_days``, fit each model on the earlier data,
    score the holdout. Returns one row per model:
    [model, mae, rmse, mape_pct, n_holdout]."""
    need = horizon_days + 2 * seasonal_periods
    if len(series) <= need:
        raise ValueError(
            f"need > {need} days to backtest a {horizon_days}-day horizon, got {len(series)}"
        )
    train = series.iloc[:-horizon_days].reset_index(drop=True)
    holdout = series.iloc[-horizon_days:].reset_index(drop=True)
    actual = holdout["revenue"].to_numpy(dtype="float64")
    last_train_date = train["date"].max()

    lm, origin = fit_model(train)
    lin_pred = forecast(lm, origin, last_train_date, horizon_days)["predicted_sales"].to_numpy()

    hw = fit_holtwinters(train, seasonal_periods=seasonal_periods)
    hw_pred = forecast_holtwinters(hw, last_train_date, horizon_days)["predicted_sales"].to_numpy()

    return pd.DataFrame(
        [
            {"model": settings.FORECAST_MODEL_VERSION, **model_errors(actual, lin_pred),
             "n_holdout": len(actual)},
            {"model": settings.FORECAST_HW_MODEL_VERSION, **model_errors(actual, hw_pred),
             "n_holdout": len(actual)},
        ]
    )


# ---------------------------------------------------------------------------
# write (append-only, versioned)
# ---------------------------------------------------------------------------
def load_forecast(engine: Engine, predictions: pd.DataFrame, model_version: str) -> int:
    md = reflect(engine)
    tbl = md.tables[f"{settings.ANALYTICS_SCHEMA}.sales_forecast"]
    rows = [
        {
            "forecast_date": row.forecast_date,
            "predicted_sales": float(row.predicted_sales),
            "model_version": model_version,
        }
        for row in predictions.itertuples(index=False)
    ]
    with engine.begin() as conn:
        conn.execute(tbl.insert(), rows)
    logger.info("Appended %d rows to sales_forecast (model_version=%s)", len(rows), model_version)
    return len(rows)


def run(engine: Engine | None = None, horizon_days: int | None = None) -> dict:
    engine = engine or get_engine("etl")
    horizon_days = horizon_days or settings.FORECAST_HORIZON_DAYS
    seasonal_periods = settings.FORECAST_SEASONAL_PERIODS
    log_run(engine, "STARTED")
    try:
        daily = read_daily_revenue(engine)
        series = build_daily_series(daily)
        if len(series) < max(14, 2 * seasonal_periods):
            raise RuntimeError(
                f"need at least {max(14, 2 * seasonal_periods)} days of history to forecast"
            )
        last_date = series["date"].max()

        # --- model 1: linear regression ------------------------------------
        lm, origin = fit_model(series)
        lin_preds = forecast(lm, origin, last_date, horizon_days)
        n_lin = load_forecast(engine, lin_preds, settings.FORECAST_MODEL_VERSION)
        trend = float(lm.coef_[FEATURE_COLUMNS.index("t")])

        # --- model 2: Holt-Winters ---------------------------------------
        hw = fit_holtwinters(series, seasonal_periods=seasonal_periods)
        hw_preds = forecast_holtwinters(hw, last_date, horizon_days)
        n_hw = load_forecast(engine, hw_preds, settings.FORECAST_HW_MODEL_VERSION)

        # --- comparison: holdout backtest --------------------------------
        try:
            bt = backtest(series, horizon_days=horizon_days, seasonal_periods=seasonal_periods)
            comparison: object = bt.to_dict("records")
            winner = str(bt.loc[bt["mae"].idxmin(), "model"])
        except ValueError as exc:
            comparison = {"skipped": str(exc)}
            winner = None

        log_run(engine, "SUCCESS", records_processed=n_lin + n_hw)
        summary = {
            "models": [settings.FORECAST_MODEL_VERSION, settings.FORECAST_HW_MODEL_VERSION],
            "history_days": len(series),
            "horizon_days": horizon_days,
            "linreg_trend_per_day": round(trend, 2),
            "holtwinters_params": hw_smoothing_params(hw),
            "backtest": comparison,
            "backtest_winner_by_mae": winner,
        }
        logger.info("Forecast summary: %s", summary)
        return summary
    except Exception as exc:
        logger.exception("Forecasting failed")
        try:
            log_run(engine, "FAILED", error_message=repr(exc))
        finally:
            raise


def main() -> None:  # pragma: no cover - thin CLI wrapper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print("forecasting complete:", run())


if __name__ == "__main__":
    main()
