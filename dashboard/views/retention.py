"""Retention page: the signup-month cohort heatmap and the average retention
curve. Read a row left-to-right for how fast that cohort lapses; read top-to-
bottom for whether newer cohorts hold up better or worse than older ones."""

from __future__ import annotations

import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import load_cohort_retention
from dashboard.theme import (
    ACCENT,
    PLOTLY_TEMPLATE,
    plotly_config,
    section,
    sequential_colorscale,
)
from dashboard.transforms import (
    avg_retention_curve,
    cohort_sizes,
    retention_counts_matrix,
    retention_matrix,
)


def _heatmap(cohorts) -> None:
    matrix = retention_matrix(cohorts)
    counts = retention_counts_matrix(cohorts).reindex(
        index=matrix.index, columns=matrix.columns
    )
    sizes = cohort_sizes(cohorts).reindex(matrix.index)

    z = matrix.to_numpy(dtype="float64")
    # customdata carries the raw numbers behind each cell's colour:
    # [retained_customers, cohort_size]
    n_grid = np.repeat(sizes.to_numpy(dtype="float64")[:, None], z.shape[1], axis=1)
    customdata = np.dstack([counts.to_numpy(dtype="float64"), n_grid])

    # go.Heatmap (rather than px.imshow) so the hover can show the counts.
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=[str(c) for c in matrix.columns],
            y=[f"{d:%Y-%m}  n={int(sizes.get(d, 0))}" for d in matrix.index],
            customdata=customdata,
            colorscale=sequential_colorscale(),
            zmin=0, zmax=1,
            texttemplate="%{z:.0%}",
            # values sit in mostly mid-to-light amber cells -> dark ink reads;
            # the rare near-zero (dark) cell trades cell-text contrast for hover.
            textfont=dict(size=10, color="#1A1A1A"),
            hovertemplate=(
                "<b>%{y}</b><br>Month %{x} after signup<br>"
                "Retention %{z:.1%}<br>"
                "%{customdata[0]:,.0f} of %{customdata[1]:,.0f} customers"
                "<extra></extra>"
            ),
            hoverongaps=False,
            colorbar=dict(title="Retained", tickformat=".0%", outlinewidth=0, thickness=12),
        )
    )
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        margin=dict(l=0, r=0, t=10, b=0),
        height=max(320, 26 * len(matrix) + 70),
        xaxis_title="Months since signup",
        yaxis_title=None,
    )
    fig.update_xaxes(side="top", showgrid=False)
    fig.update_yaxes(showgrid=False, autorange="reversed")
    st.plotly_chart(fig, width="stretch", config=plotly_config())


def render() -> None:
    st.title("Cohort Retention")

    cohorts = load_cohort_retention()
    if cohorts.empty:
        st.info("No cohort rows yet — run `python -m analytics.cohorts` (or the full pipeline).",
                icon=":material/inbox:")
        return

    n_cohorts = cohorts["cohort_month"].nunique()
    span_lo = cohorts["cohort_month"].min().date()
    span_hi = cohorts["cohort_month"].max().date()
    st.caption(
        f"{n_cohorts} signup-month cohorts, {span_lo} → {span_hi}. Cohorts whose signup "
        "predates the order history are excluded (their early months are unobservable), and "
        "each row stops at the last month the data can actually measure."
    )

    with st.container(border=True):
        section("Cohort heatmap", "grid_on")
        _heatmap(cohorts)
        st.caption(
            "Each cell is *retained ÷ cohort size* for that month — hover for the raw counts. "
            "Month 0 is the signup month itself. A steep fall from month 0 to month 1 points "
            "at onboarding; roughly flat rows mean the product holds the customers it lands."
        )

    st.write("")
    with st.container(border=True):
        section("Average retention curve", "trending_down")
        curve = avg_retention_curve(cohorts)
        fig2 = px.line(curve, x="months_since_signup", y="retention_rate", markers=True,
                       template=PLOTLY_TEMPLATE, custom_data=["n_cohorts"])
        # single summary series -> the gold emphasis accent
        fig2.update_traces(
            line=dict(color=ACCENT, width=2), marker=dict(color=ACCENT, size=7),
            hovertemplate="Retention %{y:.1%}<br>averaged over %{customdata[0]} cohorts"
                          "<extra>Month %{x}</extra>",
        )
        fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300,
                           yaxis_tickformat=".0%", yaxis_title="Mean retention",
                           xaxis_title="Months since signup", hovermode="x unified")
        fig2.update_yaxes(range=[0, 1])
        st.plotly_chart(fig2, width="stretch", config=plotly_config())
        st.caption(
            "Averaged across every cohort that reaches each month. Later points average fewer "
            "cohorts (only older cohorts have that much runway), so treat the right-hand tail "
            "as noisier than the left."
        )
