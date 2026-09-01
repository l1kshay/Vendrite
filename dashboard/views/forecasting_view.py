"""Forecasting page: the two models side by side over the same horizon, plus
the holdout backtest that scored them. The point is the trade-off -- a
transparent straight-line model vs. one that adapts to shifts in level, trend
and weekly shape -- not which one "won" on this particular dataset."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from config import settings
from dashboard.data import load_backtest, load_forecasts, load_sales
from dashboard.theme import ACCENT, FORECAST_COLORS, PLOTLY_TEMPLATE, TEXT_MUTED, money

_NOTES = {
    "linreg-v1": (
        "**Linear regression** — `revenue ~ trend + weekday`. Fully transparent: "
        "the trend is one coefficient. Extrapolates a straight line and a fixed "
        "weekly shape, so it lags any change in level or slope."
    ),
    "holtwinters-v1": (
        "**Holt-Winters** — exponential smoothing of level, trend and a 7-day "
        "season. Re-weights toward recent data, so it tracks shifts the linear "
        "model can't — at the cost of readable coefficients and a need for "
        "≥ 2 seasonal cycles of history."
    ),
}


def _forecast_chart(hist: pd.DataFrame, fc: pd.DataFrame) -> None:
    fig = px.line(hist, x="order_date", y="revenue", template=PLOTLY_TEMPLATE)
    fig.data[0].name = "Actual (last 90d)"
    fig.data[0].line.color = FORECAST_COLORS["actual"]
    for mv, grp in fc.groupby("model_version"):
        fig.add_scatter(
            x=grp["forecast_date"], y=grp["predicted_sales"], name=str(mv),
            line=dict(color=FORECAST_COLORS.get(str(mv), "#7E57C2"), width=2, dash="dash"),
        )
    if not hist.empty:
        # the "today" divider between actuals and forecast — the gold accent at
        # a literal current-period highlight
        fig.add_vline(x=hist["order_date"].max(), line_width=1.5, line_color=ACCENT,
                      annotation_text="today", annotation_position="top left",
                      annotation_font=dict(size=10, color=TEXT_MUTED))
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=380,
                      yaxis_title="Revenue", xaxis_title=None, legend_title=None)
    st.plotly_chart(fig, width="stretch")


def render() -> None:
    st.title("Forecasting — model comparison")

    sales = load_sales()
    forecasts = load_forecasts()
    if sales.empty or forecasts.empty:
        st.info("No forecast rows yet — run `python -m analytics.forecasting` (or the full pipeline).")
        return

    horizon = settings.FORECAST_HORIZON_DAYS
    hist = (
        sales.set_index("order_date")["total_amount"].resample("D").sum()
        .rename("revenue").reset_index().tail(90)
    )
    with st.container(border=True):
        st.subheader(f"Actual vs {horizon}-day forecast")
        st.caption(f"Actual daily revenue (last 90 days) vs each model's next {horizon} days.")
        _forecast_chart(hist, forecasts)

    st.write("")
    with st.container(border=True):
        st.subheader("Holdout backtest")
        bt = load_backtest()
        if bt.empty:
            st.info("No backtest rows — re-run the forecasting step to populate "
                    "`analytics.forecast_backtest`.")
        else:
            n = int(bt["n_holdout"].iloc[0])
            st.caption(
                f"Each model was re-fit on all but the last {n} days, then scored on that "
                "held-out tail. Lower is better; MAPE skips zero-revenue days."
            )
            show = bt.rename(columns={
                "model_version": "model", "mae": "MAE", "rmse": "RMSE",
                "mape_pct": "MAPE %", "n_holdout": "holdout days",
            }).drop(columns=["horizon_days"])
            st.table(show.set_index("model"))
            winner = bt.sort_values("mae")["model_version"].iloc[0]
            spread = bt["mae"].max() - bt["mae"].min()
            rel = spread / bt["mae"].min() if bt["mae"].min() else 0.0
            if rel < 0.1:
                st.info(
                    f"The two are within {rel * 100:.0f}% on MAE — on near-stationary data like "
                    "this the simpler, explainable model (`linreg-v1`) is the right default. "
                    "Holt-Winters earns its keep once the series shows a trend or level shift "
                    "to exploit."
                )
            else:
                st.info(f"`{winner}` has the lower holdout MAE here, by {money(spread)} "
                        f"({rel * 100:.0f}% of the better score).")

    st.write("")
    with st.container(border=True):
        st.subheader("When each model wins")
        c1, c2 = st.columns(2)
        c1.markdown(_NOTES["linreg-v1"])
        c2.markdown(_NOTES["holtwinters-v1"])
