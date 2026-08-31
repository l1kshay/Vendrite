"""
Vendrite dashboard (Phase 3) — Streamlit + Plotly.

Presentation only. This module reads pre-computed rows from the ``analytics``
schema through the **read-only** ``vendrite_dashboard`` DB role and renders them.
It performs no ingestion, transformation, RFM, or forecasting logic — those live
in ``etl/`` and ``analytics/`` and their results are already materialised in
``fact_sales`` / ``customer_segments`` / ``sales_forecast``.

All SQL is parameterised ``sqlalchemy.text`` against the ``analytics`` schema;
sidebar filtering is done in-memory with pandas (no SQL string building).

Run:  streamlit run dashboard/app.py

The ``streamlit-authenticator`` login gate is added in front of ``main()`` in
Phase 5 (Security) — ``main()`` is deliberately a single entry point so it can
be wrapped without touching the body.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine

from config import settings

st.set_page_config(page_title="Vendrite", page_icon="📊", layout="wide")

PLOTLY_TEMPLATE = "plotly_white"
SEGMENT_ORDER = ["Champion", "Loyal", "New", "At Risk", "Hibernating", "Needs Attention"]
SEGMENT_COLORS = {
    "Champion": "#2E7D32",
    "Loyal": "#66BB6A",
    "New": "#42A5F5",
    "At Risk": "#EF6C00",
    "Hibernating": "#9E9E9E",
    "Needs Attention": "#C62828",
}


# ===========================================================================
# Data access — read-only analytics role, cached
# ===========================================================================
@st.cache_resource(show_spinner=False)
def get_engine() -> Engine:
    from sqlalchemy import create_engine

    return create_engine(settings.dashboard_database_url(), pool_pre_ping=True)


@st.cache_data(ttl=300, show_spinner="Loading sales…")
def load_sales() -> pd.DataFrame:
    sql = text(
        """
        SELECT f.sale_id, f.order_id, f.quantity, f.total_amount,
               d.date  AS order_date, d.year, d.month, d.quarter, d.is_weekend,
               p.name  AS product, p.category,
               c.customer_id, c.name AS customer, c.region
        FROM analytics.fact_sales   f
        JOIN analytics.dim_date     d ON d.date_id    = f.date_id
        JOIN analytics.dim_product  p ON p.product_id = f.product_id
        JOIN analytics.dim_customer c ON c.customer_id = f.customer_id
        """
    )
    with get_engine().connect() as conn:
        df = pd.read_sql(sql, conn, parse_dates=["order_date"])
    df["total_amount"] = df["total_amount"].astype(float)
    df["month_start"] = df["order_date"].values.astype("datetime64[M]")
    return df


@st.cache_data(ttl=300, show_spinner=False)
def load_segments() -> pd.DataFrame:
    # DISTINCT ON keeps the most recent cohort row per customer.
    sql = text(
        """
        SELECT DISTINCT ON (s.customer_id)
               s.customer_id, s.recency, s.frequency, s.monetary,
               s.segment_label, s.computed_date,
               c.name AS customer, c.email, c.region
        FROM analytics.customer_segments s
        JOIN analytics.dim_customer c ON c.customer_id = s.customer_id
        ORDER BY s.customer_id, s.computed_date DESC, s.segment_id DESC
        """
    )
    with get_engine().connect() as conn:
        df = pd.read_sql(sql, conn, parse_dates=["computed_date"])
    for col in ("recency", "frequency"):
        df[col] = df[col].astype("Int64")
    df["monetary"] = df["monetary"].astype(float)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def load_forecast() -> pd.DataFrame:
    sql = text(
        """
        SELECT forecast_date, predicted_sales, model_version, generated_date
        FROM analytics.sales_forecast
        WHERE generated_date = (SELECT max(generated_date) FROM analytics.sales_forecast)
        ORDER BY forecast_date
        """
    )
    with get_engine().connect() as conn:
        df = pd.read_sql(sql, conn, parse_dates=["forecast_date", "generated_date"])
    if not df.empty:
        df["predicted_sales"] = df["predicted_sales"].astype(float)
    return df


@st.cache_data(ttl=120, show_spinner=False)
def load_run_log() -> pd.DataFrame:
    sql = text(
        """
        SELECT run_id, run_timestamp, status, records_processed, error_message
        FROM analytics.etl_run_log
        ORDER BY run_id DESC
        LIMIT 25
        """
    )
    with get_engine().connect() as conn:
        return pd.read_sql(sql, conn, parse_dates=["run_timestamp"])


# ===========================================================================
# Small helpers
# ===========================================================================
def _money(x: float) -> str:
    return f"${x:,.0f}"


def kpi_block(df: pd.DataFrame, prev: pd.DataFrame) -> None:
    """Five KPI cards with deltas vs the equal-length preceding period."""
    def agg(frame: pd.DataFrame) -> dict:
        return {
            "revenue": float(frame["total_amount"].sum()),
            "orders": int(frame["order_id"].nunique()),
            "units": int(frame["quantity"].sum()),
            "aov": float(frame.groupby("order_id")["total_amount"].sum().mean()) if len(frame) else 0.0,
            "customers": int(frame["customer_id"].nunique()),
        }

    cur, pre = agg(df), agg(prev)
    cols = st.columns(5)
    specs = [
        ("Revenue", _money(cur["revenue"]), cur["revenue"] - pre["revenue"], _money),
        ("Orders", f"{cur['orders']:,}", cur["orders"] - pre["orders"], lambda v: f"{v:+,}"),
        ("Units sold", f"{cur['units']:,}", cur["units"] - pre["units"], lambda v: f"{v:+,}"),
        ("Avg order value", _money(cur["aov"]), cur["aov"] - pre["aov"], lambda v: f"{v:+,.0f}"),
        ("Active customers", f"{cur['customers']:,}", cur["customers"] - pre["customers"], lambda v: f"{v:+,}"),
    ]
    for col, (label, value, delta, fmt) in zip(cols, specs):
        col.metric(label, value, fmt(delta) if prev is not None and len(prev) else None)


def sales_trend_chart(df: pd.DataFrame, grain: str) -> None:
    freq = {"Daily": "D", "Weekly": "W-MON", "Monthly": "MS"}[grain]
    ts = (
        df.set_index("order_date")["total_amount"]
        .resample(freq)
        .sum()
        .rename("revenue")
        .reset_index()
    )
    fig = px.area(ts, x="order_date", y="revenue", template=PLOTLY_TEMPLATE)
    if grain == "Daily" and len(ts) > 7:
        ts["7-day avg"] = ts["revenue"].rolling(7, min_periods=1).mean()
        fig.add_scatter(x=ts["order_date"], y=ts["7-day avg"], name="7-day avg",
                        line=dict(color="#1A237E", width=2))
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=320,
                      yaxis_title="Revenue", xaxis_title=None, showlegend=True)
    st.plotly_chart(fig, width='stretch')


def forecast_chart(sales: pd.DataFrame, forecast: pd.DataFrame) -> None:
    hist = (
        sales.set_index("order_date")["total_amount"].resample("D").sum()
        .rename("revenue").reset_index().tail(90)
    )
    fig = px.line(hist, x="order_date", y="revenue", template=PLOTLY_TEMPLATE)
    fig.data[0].name = "Actual (last 90d)"
    fig.data[0].line.color = "#90A4AE"
    if not forecast.empty:
        fig.add_scatter(
            x=forecast["forecast_date"], y=forecast["predicted_sales"],
            name=f"Forecast ({forecast['model_version'].iloc[0]})",
            line=dict(color="#C62828", width=2, dash="dash"),
        )
        fig.add_vline(x=hist["order_date"].max(), line_width=1,
                      line_dash="dot", line_color="#B0BEC5")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=320,
                      yaxis_title="Revenue", xaxis_title=None)
    st.plotly_chart(fig, width='stretch')


def category_region_charts(df: pd.DataFrame) -> None:
    left, right = st.columns(2)
    by_cat = df.groupby("category", as_index=False)["total_amount"].sum().sort_values("total_amount")
    fig1 = px.bar(by_cat, x="total_amount", y="category", orientation="h",
                  template=PLOTLY_TEMPLATE, text_auto=".2s")
    fig1.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300,
                       xaxis_title="Revenue", yaxis_title=None)
    left.subheader("Revenue by category")
    left.plotly_chart(fig1, width='stretch')

    by_reg = df.groupby("region", as_index=False)["total_amount"].sum().sort_values("total_amount")
    fig2 = px.bar(by_reg, x="total_amount", y="region", orientation="h",
                  template=PLOTLY_TEMPLATE, text_auto=".2s", color_discrete_sequence=["#42A5F5"])
    fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300,
                       xaxis_title="Revenue", yaxis_title=None)
    right.subheader("Revenue by region")
    right.plotly_chart(fig2, width='stretch')


def segment_section(seg: pd.DataFrame) -> None:
    st.subheader("Customer segments (RFM)")
    if seg.empty:
        st.info("No segmentation rows yet — run `python -m analytics.segmentation`.")
        return

    cohort = pd.to_datetime(seg["computed_date"]).max().date()
    st.caption(f"Latest cohort: {cohort} · {len(seg):,} customers")

    left, right = st.columns([2, 3])
    counts = (
        seg["segment_label"].value_counts()
        .reindex(SEGMENT_ORDER).dropna().rename_axis("segment").reset_index(name="customers")
    )
    fig = px.bar(counts, x="customers", y="segment", orientation="h", template=PLOTLY_TEMPLATE,
                 color="segment", color_discrete_map=SEGMENT_COLORS, text="customers")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300, showlegend=False,
                      xaxis_title="Customers", yaxis_title=None,
                      yaxis=dict(categoryorder="array", categoryarray=SEGMENT_ORDER[::-1]))
    left.plotly_chart(fig, width='stretch')

    profile = (
        seg.groupby("segment_label")
        .agg(customers=("customer_id", "size"),
             avg_recency=("recency", "mean"),
             avg_frequency=("frequency", "mean"),
             avg_monetary=("monetary", "mean"))
        .reindex(SEGMENT_ORDER).dropna(how="all")
        .round({"avg_recency": 0, "avg_frequency": 1, "avg_monetary": 0})
    )
    right.dataframe(profile, width='stretch')

    # ---- drill-down: customers within a chosen segment ----
    pick = st.selectbox("Drill into a segment", SEGMENT_ORDER, index=0)
    members = (
        seg[seg["segment_label"] == pick]
        .loc[:, ["customer_id", "customer", "region", "recency", "frequency", "monetary"]]
        .sort_values("monetary", ascending=False)
        .reset_index(drop=True)
    )
    st.caption(f"{len(members):,} customers in **{pick}**")
    st.dataframe(members, width='stretch', height=280)
    st.download_button(
        f"Download {pick} customers (CSV)",
        members.to_csv(index=False).encode(),
        file_name=f"vendrite_segment_{pick.lower().replace(' ', '_')}.csv",
        mime="text/csv",
    )


def category_drilldown(df: pd.DataFrame) -> None:
    st.subheader("Category drill-down")
    cats = sorted(df["category"].dropna().unique())
    if not cats:
        return
    cat = st.selectbox("Category", cats, index=0)
    sub = df[df["category"] == cat]

    monthly = sub.groupby("month_start", as_index=False)["total_amount"].sum()
    fig = px.bar(monthly, x="month_start", y="total_amount", template=PLOTLY_TEMPLATE)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=280,
                      xaxis_title=None, yaxis_title="Revenue")
    st.plotly_chart(fig, width='stretch')

    top = (
        sub.groupby("product")
        .agg(revenue=("total_amount", "sum"), units=("quantity", "sum"), orders=("order_id", "nunique"))
        .sort_values("revenue", ascending=False)
        .head(10)
        .round({"revenue": 2})
    )
    st.caption(f"Top products — {cat}")
    st.dataframe(top, width='stretch')


# ===========================================================================
# Main
# ===========================================================================
def main() -> None:
    st.title("📊 Vendrite — Sales & Customer Analytics")

    try:
        sales = load_sales()
    except Exception as exc:  # noqa: BLE001 — surface config/DB errors cleanly
        st.error(
            "Could not load data from the analytics warehouse.\n\n"
            f"`{type(exc).__name__}: {exc}`\n\n"
            "Check that PostgreSQL is running, the schema is applied, the ETL has "
            "been run, and `.env` has the `VENDRITE_DASHBOARD_DB_*` values."
        )
        st.stop()

    if sales.empty:
        st.warning("`analytics.fact_sales` is empty — run `python -m etl.run_etl --generate` first.")
        st.stop()

    segments = load_segments()
    forecast = load_forecast()

    # ---- sidebar filters ----
    st.sidebar.header("Filters")
    dmin, dmax = sales["order_date"].min().date(), sales["order_date"].max().date()
    date_range = st.sidebar.date_input(
        "Date range", value=(dmin, dmax), min_value=dmin, max_value=dmax
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start, end = date_range
    else:
        start, end = dmin, dmax

    all_categories = sorted(sales["category"].dropna().unique())
    all_regions = sorted(sales["region"].dropna().unique())
    pick_cats = st.sidebar.multiselect("Category", all_categories, default=all_categories)
    pick_regs = st.sidebar.multiselect("Region", all_regions, default=all_regions)

    mask = (
        (sales["order_date"].dt.date >= start)
        & (sales["order_date"].dt.date <= end)
        & (sales["category"].isin(pick_cats))
        & (sales["region"].isin(pick_regs))
    )
    view = sales[mask]

    # equal-length preceding period for KPI deltas
    span = (end - start) + dt.timedelta(days=1)
    prev_mask = (
        (sales["order_date"].dt.date >= start - span)
        & (sales["order_date"].dt.date < start)
        & (sales["category"].isin(pick_cats))
        & (sales["region"].isin(pick_regs))
    )
    prev_view = sales[prev_mask]

    st.caption(
        f"Showing **{start} → {end}**  ·  {len(view):,} order lines  ·  "
        f"{len(pick_cats)}/{len(all_categories)} categories  ·  {len(pick_regs)}/{len(all_regions)} regions"
    )
    if view.empty:
        st.warning("No sales match the current filters.")
        st.stop()

    kpi_block(view, prev_view)
    st.divider()

    grain = st.radio("Trend grain", ["Daily", "Weekly", "Monthly"], horizontal=True, index=1)
    st.subheader("Sales trend")
    sales_trend_chart(view, grain)

    st.subheader(f"{settings.FORECAST_HORIZON_DAYS}-day forecast")
    forecast_chart(sales, forecast)
    st.divider()

    category_region_charts(view)
    st.divider()

    segment_section(segments)
    st.divider()

    category_drilldown(view)

    # ---- footer: pipeline freshness ----
    with st.expander("Pipeline status"):
        runs = load_run_log()
        st.dataframe(runs, width='stretch', height=240)


if __name__ == "__main__":
    main()
