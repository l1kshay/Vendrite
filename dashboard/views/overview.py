"""Overview page: headline KPIs and the sales trend, plus category / region
breakdowns and a category drill-down. All react to the sidebar filters."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.express as px
import streamlit as st

from config import settings
from dashboard.data import load_run_log, load_sales
from dashboard.theme import (
    ACCENT,
    PLOTLY_TEMPLATE,
    kpi_card,
    money,
    plotly_config,
    presentation_mode,
    section,
)


def _sidebar_filters(sales: pd.DataFrame):
    # Collapsed by default in presentation mode, open otherwise — but always
    # visible and usable. key= drives the `.st-key-vd-filters` ordering hook
    # in theme.py; the chevron is Streamlit's own Material icon.
    exp = st.sidebar.expander(
        "Filters", expanded=not presentation_mode(),
        icon=":material/tune:", key="vd-filters",
    )
    with exp:
        dmin, dmax = sales["order_date"].min().date(), sales["order_date"].max().date()
        date_range = st.date_input(
            "Date range", value=(dmin, dmax), min_value=dmin, max_value=dmax
        )
        start, end = (
            date_range
            if isinstance(date_range, tuple) and len(date_range) == 2
            else (dmin, dmax)
        )

        cats = sorted(sales["category"].dropna().unique())
        regs = sorted(sales["region"].dropna().unique())
        pick_cats = st.multiselect("Category", cats, default=cats)
        pick_regs = st.multiselect("Region", regs, default=regs)
    return start, end, cats, regs, pick_cats, pick_regs


def _signed_money(v: float) -> str:
    return f"{'+' if v >= 0 else '−'}${abs(v):,.0f}"


def _kpis(df: pd.DataFrame, prev: pd.DataFrame) -> None:
    def agg(frame: pd.DataFrame) -> dict:
        return {
            "revenue": float(frame["total_amount"].sum()),
            "orders": int(frame["order_id"].nunique()),
            "units": int(frame["quantity"].sum()),
            "aov": float(frame.groupby("order_id")["total_amount"].sum().mean()) if len(frame) else 0.0,
            "customers": int(frame["customer_id"].nunique()),
        }

    cur, pre = agg(df), agg(prev)
    has_prev = prev is not None and len(prev) > 0
    count = lambda v: f"{'+' if v >= 0 else '−'}{abs(v):,.0f}"  # noqa: E731
    specs = [
        ("Revenue", "payments", money(cur["revenue"]), cur["revenue"] - pre["revenue"], _signed_money),
        ("Orders", "receipt_long", f"{cur['orders']:,}", cur["orders"] - pre["orders"], count),
        ("Units sold", "inventory_2", f"{cur['units']:,}", cur["units"] - pre["units"], count),
        ("Avg order value", "sell", money(cur["aov"]), cur["aov"] - pre["aov"], _signed_money),
        ("Active customers", "group", f"{cur['customers']:,}", cur["customers"] - pre["customers"], count),
    ]
    for col, (label, ico, value, delta, fmt) in zip(st.columns(5), specs):
        kpi_card(
            col, label=label, icon_name=ico, value=value,
            delta=fmt(delta) if has_prev else None,
            direction=(0 if not has_prev else (1 if delta > 0 else -1 if delta < 0 else 0)),
        )


def _trend_chart(df: pd.DataFrame, grain: str) -> None:
    freq = {"Daily": "D", "Weekly": "W-MON", "Monthly": "MS"}[grain]
    ts = df.set_index("order_date")["total_amount"].resample(freq).sum().rename("revenue").reset_index()
    fig = px.area(ts, x="order_date", y="revenue", template=PLOTLY_TEMPLATE)
    fig.update_traces(name="Revenue", hovertemplate="$%{y:,.0f}<extra>Revenue</extra>")
    if grain == "Daily" and len(ts) > 7:
        ts["7-day avg"] = ts["revenue"].rolling(7, min_periods=1).mean()
        # gold emphasis line — the identity accent at a genuine highlight point
        fig.add_scatter(x=ts["order_date"], y=ts["7-day avg"], name="7-day avg",
                        line=dict(color=ACCENT, width=2),
                        hovertemplate="$%{y:,.0f}<extra>7-day avg</extra>")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=320,
                      yaxis_title="Revenue", xaxis_title=None, showlegend=True,
                      hovermode="x unified")
    fig.update_xaxes(hoverformat="%b %d, %Y")
    st.plotly_chart(fig, width="stretch", config=plotly_config())


def _category_region(df: pd.DataFrame) -> None:
    left, right = st.columns(2)
    by_cat = df.groupby("category", as_index=False)["total_amount"].sum().sort_values("total_amount")
    fig1 = px.bar(by_cat, x="total_amount", y="category", orientation="h",
                  template=PLOTLY_TEMPLATE, text_auto=".2s")
    fig1.update_traces(hovertemplate="<b>%{y}</b><br>Revenue $%{x:,.0f}<extra></extra>")
    fig1.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300, xaxis_title="Revenue", yaxis_title=None)
    with left:
        section("Revenue by category", "category")
        st.plotly_chart(fig1, width="stretch", config=plotly_config())

    by_reg = df.groupby("region", as_index=False)["total_amount"].sum().sort_values("total_amount")
    fig2 = px.bar(by_reg, x="total_amount", y="region", orientation="h",
                  template=PLOTLY_TEMPLATE, text_auto=".2s")
    fig2.update_traces(hovertemplate="<b>%{y}</b><br>Revenue $%{x:,.0f}<extra></extra>")
    fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300, xaxis_title="Revenue", yaxis_title=None)
    with right:
        section("Revenue by region", "public")
        st.plotly_chart(fig2, width="stretch", config=plotly_config())


def _category_drilldown(df: pd.DataFrame) -> None:
    section("Category drill-down", "search")
    cats = sorted(df["category"].dropna().unique())
    if not cats:
        return
    cat = st.selectbox("Category", cats, index=0)
    sub = df[df["category"] == cat]

    monthly = sub.groupby("month_start", as_index=False)["total_amount"].sum()
    fig = px.bar(monthly, x="month_start", y="total_amount", template=PLOTLY_TEMPLATE)
    fig.update_traces(hovertemplate="<b>%{x|%b %Y}</b><br>Revenue $%{y:,.0f}<extra></extra>")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=280, xaxis_title=None, yaxis_title="Revenue")
    st.plotly_chart(fig, width="stretch", config=plotly_config())

    top = (
        sub.groupby("product")
        .agg(revenue=("total_amount", "sum"), units=("quantity", "sum"), orders=("order_id", "nunique"))
        .sort_values("revenue", ascending=False)
        .head(10)
        .round({"revenue": 2})
    )
    st.caption(f"Top products — {cat}")
    st.dataframe(top, width="stretch")


def render() -> None:
    st.title("Overview")
    st.caption("Headline performance for the selected window. Use the sidebar to narrow by date, category or region.")

    try:
        sales = load_sales()
    except Exception as exc:  # noqa: BLE001
        st.error(
            "Could not load data from the analytics warehouse.\n\n"
            f"`{type(exc).__name__}: {exc}`\n\n"
            "Check PostgreSQL is up, the schema is applied, the ETL has run, and "
            "`.env` has the `VENDRITE_DASHBOARD_DB_*` values.",
            icon=":material/error:",
        )
        st.stop()
    if sales.empty:
        st.warning("`analytics.fact_sales` is empty — run `python -m etl.run_etl --generate` first.",
                   icon=":material/inbox:")
        st.stop()

    start, end, cats, regs, pick_cats, pick_regs = _sidebar_filters(sales)
    mask = (
        (sales["order_date"].dt.date >= start) & (sales["order_date"].dt.date <= end)
        & (sales["category"].isin(pick_cats)) & (sales["region"].isin(pick_regs))
    )
    view = sales[mask]

    span = (end - start) + dt.timedelta(days=1)
    prev_view = sales[
        (sales["order_date"].dt.date >= start - span) & (sales["order_date"].dt.date < start)
        & (sales["category"].isin(pick_cats)) & (sales["region"].isin(pick_regs))
    ]

    st.caption(
        f"**{start} → {end}** · {len(view):,} order lines · "
        f"{len(pick_cats)}/{len(cats)} categories · {len(pick_regs)}/{len(regs)} regions"
    )
    if view.empty:
        st.warning("No sales match the current filters.", icon=":material/filter_alt_off:")
        st.stop()

    _kpis(view, prev_view)
    rev_now = float(view["total_amount"].sum())
    rev_prev = float(prev_view["total_amount"].sum())
    if rev_prev > 0:
        chg = (rev_now - rev_prev) / rev_prev
        st.caption(
            f"Revenue is {money(rev_now)} over {span.days} days — "
            f"{'up' if chg >= 0 else 'down'} {abs(chg) * 100:.1f}% vs the preceding {span.days} days."
        )

    st.write("")
    with st.container(border=True):
        top = st.columns([3, 1])
        with top[0]:
            section("Sales trend", "show_chart")
        grain = top[1].radio("Grain", ["Daily", "Weekly", "Monthly"], index=1,
                             label_visibility="collapsed")
        _trend_chart(view, grain)
        st.caption(
            "Weekly grain smooths day-of-week noise; on the daily view the gold line is a 7-day "
            "moving average — read that, not the spiky raw series, for the underlying direction."
        )

    st.write("")
    with st.container(border=True):
        _category_region(view)
        st.caption("Where the revenue concentrates. A category or region carrying most of the "
                   "total is both the growth lever and the concentration risk.")

    st.write("")
    with st.container(border=True):
        _category_drilldown(view)

    with st.expander("Pipeline status"):
        st.dataframe(load_run_log(), width="stretch", height=240)
