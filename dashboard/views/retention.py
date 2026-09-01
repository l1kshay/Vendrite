"""Retention page: the signup-month cohort heatmap and the average retention
curve. Read a row left-to-right for how fast that cohort lapses; read top-to-
bottom for whether newer cohorts hold up better or worse than older ones."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from dashboard.data import load_cohort_retention
from dashboard.theme import PLOTLY_TEMPLATE, SEQUENTIAL_SCALE
from dashboard.transforms import avg_retention_curve, cohort_sizes, retention_matrix


def render() -> None:
    st.title("Cohort Retention")

    cohorts = load_cohort_retention()
    if cohorts.empty:
        st.info("No cohort rows yet — run `python -m analytics.cohorts` (or the full pipeline).")
        return

    n_cohorts = cohorts["cohort_month"].nunique()
    span_lo = cohorts["cohort_month"].min().date()
    span_hi = cohorts["cohort_month"].max().date()
    st.caption(
        f"{n_cohorts} signup-month cohorts, {span_lo} → {span_hi}. Cohorts whose signup "
        "predates the order history are excluded (their early months are unobservable), and "
        "each row stops at the last month the data can actually measure."
    )

    matrix = retention_matrix(cohorts)
    sizes = cohort_sizes(cohorts)
    labels = [f"{d}  (n={sizes.get(d, 0)})" for d in matrix.index]

    fig = px.imshow(
        matrix.to_numpy(),
        x=[str(c) for c in matrix.columns],
        y=labels,
        color_continuous_scale=SEQUENTIAL_SCALE,
        zmin=0, zmax=1,
        aspect="auto",
        text_auto=".0%",
        template=PLOTLY_TEMPLATE,
    )
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0),
                      height=max(320, 26 * len(matrix) + 60),
                      xaxis_title="Months since signup", yaxis_title=None,
                      coloraxis_colorbar=dict(title="Retained", tickformat=".0%"))
    fig.update_xaxes(side="top")
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Each cell is *retained ÷ cohort size* for that month. Month 0 is the signup month "
        "itself. A steep fall from month 0 to month 1 points at onboarding; roughly flat rows "
        "mean the product holds the customers it lands."
    )

    st.divider()
    st.subheader("Average retention curve")
    curve = avg_retention_curve(cohorts)
    fig2 = px.line(curve, x="months_since_signup", y="retention_rate", markers=True,
                   template=PLOTLY_TEMPLATE)
    fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300,
                       yaxis_tickformat=".0%", yaxis_title="Mean retention",
                       xaxis_title="Months since signup")
    fig2.update_yaxes(range=[0, 1])
    st.plotly_chart(fig2, width="stretch")
    st.caption(
        "Averaged across every cohort that reaches each month. Later points average fewer "
        "cohorts (only older cohorts have that much runway), so treat the right-hand tail as "
        "noisier than the left."
    )
