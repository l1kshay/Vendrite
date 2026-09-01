"""
Dashboard data-access layer.

The ONLY place the dashboard talks to PostgreSQL. Every query is a
parameterised ``sqlalchemy.text`` against the ``analytics`` schema, run through
the **read-only** ``vendrite_dashboard`` role. Results are cached with
``st.cache_data`` (short TTL) so page switches don't re-hit the database.

View modules import from here and never build SQL of their own.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import settings


@st.cache_resource(show_spinner=False)
def get_engine() -> Engine:
    return create_engine(settings.dashboard_database_url(), pool_pre_ping=True)


def _read(sql: str, params: dict | None = None, parse_dates: list[str] | None = None) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {}, parse_dates=parse_dates)


# ---------------------------------------------------------------------------
# sales fact (joined to all dimensions)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner="Loading sales…")
def load_sales() -> pd.DataFrame:
    df = _read(
        """
        SELECT f.sale_id, f.order_id, f.quantity, f.total_amount,
               d.date  AS order_date, d.year, d.month, d.quarter, d.is_weekend,
               p.name  AS product, p.category,
               c.customer_id, c.name AS customer, c.region
        FROM analytics.fact_sales   f
        JOIN analytics.dim_date     d ON d.date_id     = f.date_id
        JOIN analytics.dim_product  p ON p.product_id  = f.product_id
        JOIN analytics.dim_customer c ON c.customer_id = f.customer_id
        """,
        parse_dates=["order_date"],
    )
    df["total_amount"] = df["total_amount"].astype(float)
    df["month_start"] = df["order_date"].values.astype("datetime64[M]")
    return df


# ---------------------------------------------------------------------------
# RFM segments (latest cohort row per customer)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def load_segments() -> pd.DataFrame:
    df = _read(
        """
        SELECT DISTINCT ON (s.customer_id)
               s.customer_id, s.recency, s.frequency, s.monetary,
               s.segment_label, s.computed_date,
               c.name AS customer, c.email, c.region
        FROM analytics.customer_segments s
        JOIN analytics.dim_customer c ON c.customer_id = s.customer_id
        ORDER BY s.customer_id, s.computed_date DESC, s.segment_id DESC
        """,
        parse_dates=["computed_date"],
    )
    for col in ("recency", "frequency"):
        df[col] = df[col].astype("Int64")
    df["monetary"] = df["monetary"].astype(float)
    return df


# ---------------------------------------------------------------------------
# CLV (latest row per customer)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def load_clv() -> pd.DataFrame:
    df = _read(
        """
        SELECT DISTINCT ON (v.customer_id)
               v.customer_id, v.avg_order_value, v.purchase_freq_annual,
               v.avg_lifespan_years, v.gross_margin, v.predicted_clv,
               v.method_version, v.computed_date
        FROM analytics.customer_clv v
        ORDER BY v.customer_id, v.computed_date DESC, v.clv_id DESC
        """,
        parse_dates=["computed_date"],
    )
    for col in ("avg_order_value", "purchase_freq_annual", "avg_lifespan_years",
                "gross_margin", "predicted_clv"):
        df[col] = df[col].astype(float)
    return df


# ---------------------------------------------------------------------------
# cohort retention (latest computed_date grid)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def load_cohort_retention() -> pd.DataFrame:
    df = _read(
        """
        SELECT cohort_month, months_since_signup, cohort_size,
               retained_customers, retention_rate
        FROM analytics.cohort_retention
        WHERE computed_date = (SELECT max(computed_date) FROM analytics.cohort_retention)
        ORDER BY cohort_month, months_since_signup
        """,
        parse_dates=["cohort_month"],
    )
    if not df.empty:
        df["retention_rate"] = df["retention_rate"].astype(float)
    return df


# ---------------------------------------------------------------------------
# forecasts
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def load_forecasts() -> pd.DataFrame:
    """All model_versions from the most recent forecast run (per model)."""
    df = _read(
        """
        SELECT s.forecast_date, s.predicted_sales, s.model_version, s.generated_date
        FROM analytics.sales_forecast s
        JOIN (
            SELECT model_version, max(generated_date) AS g
            FROM analytics.sales_forecast
            GROUP BY model_version
        ) latest
          ON latest.model_version = s.model_version AND latest.g = s.generated_date
        ORDER BY s.model_version, s.forecast_date
        """,
        parse_dates=["forecast_date", "generated_date"],
    )
    if not df.empty:
        df["predicted_sales"] = df["predicted_sales"].astype(float)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def load_forecast(model_version: str | None = None) -> pd.DataFrame:
    """One model's most recent horizon (defaults to the incumbent linreg)."""
    mv = model_version or settings.FORECAST_MODEL_VERSION
    df = load_forecasts()
    return df[df["model_version"] == mv].reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_backtest() -> pd.DataFrame:
    """Per-model holdout-backtest scores from the most recent run."""
    df = _read(
        """
        SELECT model_version, horizon_days, n_holdout, mae, rmse, mape_pct
        FROM analytics.forecast_backtest
        WHERE generated_date = (SELECT max(generated_date) FROM analytics.forecast_backtest)
        ORDER BY mae
        """,
    )
    for col in ("mae", "rmse", "mape_pct"):
        if col in df:
            df[col] = df[col].astype(float)
    return df


# ---------------------------------------------------------------------------
# pipeline freshness
# ---------------------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def load_run_log() -> pd.DataFrame:
    return _read(
        """
        SELECT run_id, run_timestamp, status, records_processed, error_message
        FROM analytics.etl_run_log
        ORDER BY run_id DESC
        LIMIT 25
        """,
        parse_dates=["run_timestamp"],
    )
