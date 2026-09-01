"""
Short-term sales forecasting (Phase 2).

Reads ONLY from the ``analytics`` schema, aggregates ``fact_sales`` to a daily
revenue time series, fits an **explainable** ordinary-least-squares linear
regression (scikit-learn ``LinearRegression``), predicts the next
``VENDRITE_FORECAST_HORIZON_DAYS`` days, and APPENDS the predictions to
``analytics.sales_forecast`` tagged with ``model_version`` (the table is
standalone -- no FK to fact_sales -- so forecast versions are independent).

Model (kept simple / interpretable, not a black box)
---------------------------------------------------
    revenue(day) ~ b0 + b_t * t + sum_k b_dow_k * [weekday == k]

* ``t``       = integer day offset from the first observed day -> the slope
                ``b_t`` is the average revenue trend per day.
* ``dow_1..6``= one-hot weekday flags (Monday=0 is the baseline), so the model
                captures a weekly shape without any non-linearity.

Predicted revenue is clipped at 0 (negative sales are not meaningful).

Pure functions (``build_daily_series``, ``make_features``, ``fit_model``,
``forecast``) are unit-tested.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

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
# pure computation
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
    log_run(engine, "STARTED")
    try:
        daily = read_daily_revenue(engine)
        series = build_daily_series(daily)
        if len(series) < 14:
            raise RuntimeError("need at least 14 days of sales history to forecast")

        model, origin = fit_model(series)
        last_date = series["date"].max()
        preds = forecast(model, origin, last_date, horizon_days)
        n = load_forecast(engine, preds, settings.FORECAST_MODEL_VERSION)

        trend = float(model.coef_[FEATURE_COLUMNS.index("t")])
        log_run(engine, "SUCCESS", records_processed=n)
        summary = {
            "model_version": settings.FORECAST_MODEL_VERSION,
            "history_days": len(series),
            "trend_per_day": round(trend, 2),
            "horizon_days": horizon_days,
            "first_forecast": preds.iloc[0].to_dict(),
            "last_forecast": preds.iloc[-1].to_dict(),
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
