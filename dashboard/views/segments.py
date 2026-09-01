"""Customer Segments page: the RFM segmentation and the heuristic CLV, shown
together. RFM measures recent engagement; CLV projects forward value. The
off-diagonal customers -- frequent but low-value, or valuable but slipping --
are the ones worth acting on."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from dashboard.data import load_clv, load_segments
from dashboard.theme import (
    BORDER_STRONG,
    PLOTLY_TEMPLATE,
    QUADRANT_ORDER,
    SEGMENT_COLORS,
    SEGMENT_ORDER,
    TEXT_MUTED,
    money,
)
from dashboard.transforms import assign_quadrants, quadrant_summary, rfm_clv_frame


def _distribution(seg) -> None:
    counts = (
        seg["segment_label"].value_counts()
        .reindex(SEGMENT_ORDER).dropna().rename_axis("segment").reset_index(name="customers")
    )
    fig = px.bar(counts, x="customers", y="segment", orientation="h", template=PLOTLY_TEMPLATE,
                 color="segment", color_discrete_map=SEGMENT_COLORS, text="customers")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=280, showlegend=False,
                      xaxis_title="Customers", yaxis_title=None,
                      yaxis=dict(categoryorder="array", categoryarray=SEGMENT_ORDER[::-1]))
    st.plotly_chart(fig, width="stretch")


def _rfm_profile(seg):
    return (
        seg.groupby("segment_label")
        .agg(customers=("customer_id", "size"),
             avg_recency=("recency", "mean"),
             avg_frequency=("frequency", "mean"),
             avg_monetary=("monetary", "mean"))
        .reindex(SEGMENT_ORDER).dropna(how="all")
        .round({"avg_recency": 0, "avg_frequency": 1, "avg_monetary": 0})
    )


_CORNERS = [  # (x, y, text) in paper coords — quadrant names carried by position
    (0.99, 0.98, "PROTECT"),
    (0.01, 0.98, "WIN BACK"),
    (0.99, 0.03, "UPSELL"),
    (0.01, 0.03, "LOW PRIORITY"),
]


def _quadrant_scatter(rc) -> None:
    r_cut = float(rc["rfm_score"].median())
    c_cut = float(rc["predicted_clv"].median())
    # Colour by SEGMENT (a validated semantic palette); the four quadrants are
    # read off the median crosshair + the corner labels, not a 4th colour axis
    # (four categorical hues can't clear the scatter all-pairs CVD gate).
    fig = px.scatter(
        rc, x="rfm_score", y="predicted_clv", color="segment_label",
        category_orders={"segment_label": SEGMENT_ORDER},
        color_discrete_map=SEGMENT_COLORS, template=PLOTLY_TEMPLATE,
        hover_data=["customer", "segment_label", "monetary", "purchase_freq_annual"],
        log_y=True, opacity=0.8,
    )
    fig.update_traces(marker=dict(size=7, line=dict(width=0)))
    fig.add_vline(x=r_cut, line_width=1, line_dash="dot", line_color=BORDER_STRONG)
    fig.add_hline(y=c_cut, line_width=1, line_dash="dot", line_color=BORDER_STRONG)
    for x, y, txt in _CORNERS:
        fig.add_annotation(x=x, y=y, xref="paper", yref="paper", text=txt,
                           showarrow=False, font=dict(size=10, color=TEXT_MUTED),
                           xanchor="right" if x > 0.5 else "left")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=420,
                      xaxis_title="RFM score (recent engagement)",
                      yaxis_title="Predicted CLV (log)", legend_title=None)
    st.plotly_chart(fig, width="stretch")


def render() -> None:
    st.title("Customer Segments — RFM × CLV")

    seg = load_segments()
    clv = load_clv()
    if seg.empty or clv.empty:
        st.info("No segmentation / CLV rows yet — run `python -m analytics.segmentation` "
                "and `python -m analytics.clv` (or the full pipeline).")
        return

    cohort = seg["computed_date"].max().date()
    st.caption(f"Latest cohort: {cohort} · {len(seg):,} customers · "
               f"CLV lifespan assumption {clv['avg_lifespan_years'].iloc[0]:.1f}y, "
               f"margin {clv['gross_margin'].iloc[0]:.0%}")

    left, right = st.columns([2, 3])
    with left:
        with st.container(border=True):
            st.subheader("RFM segment mix")
            _distribution(seg)
    with right:
        with st.container(border=True):
            st.subheader("RFM profile by segment")
            st.table(_rfm_profile(seg))
            st.caption("Recency in days (lower is better), frequency in orders, monetary in $ — "
                       "the averages the segment rules act on.")

    st.write("")
    rc = assign_quadrants(rfm_clv_frame(seg, clv))
    with st.container(border=True):
        st.subheader("RFM engagement vs projected value")
        _quadrant_scatter(rc)
        st.caption(
            "Dotted lines are the medians of each axis. **Protect** (top-right) are high on both "
            "— guard them. **Win back** (top-left) score low on recent engagement but high "
            "projected value — the lapsing-VIP list. **Upsell** (bottom-right) buy often but "
            "cheaply. Because CLV annualises frequency, it rewards *current velocity* where RFM's "
            "monetary score rewards *cumulative* spend — that's why the two disagree."
        )
        summ = quadrant_summary(rc)
        st.table(
            summ.assign(
                total_clv=summ["total_clv"].map(money),
                median_clv=summ["median_clv"].map(money),
            )
        )

    st.write("")
    st.subheader("Drill in")
    by = st.radio("Group by", ["Quadrant", "RFM segment"], horizontal=True)
    if by == "Quadrant":
        pick = st.selectbox("Quadrant", QUADRANT_ORDER, index=0)
        members = rc[rc["quadrant"] == pick]
        slug = pick.split(" (")[0].lower().replace(" ", "_")
    else:
        pick = st.selectbox("Segment", SEGMENT_ORDER, index=0)
        members = rc[rc["segment_label"] == pick]
        slug = pick.lower().replace(" ", "_")

    members = (
        members.loc[:, ["customer_id", "customer", "region", "segment_label",
                        "recency", "frequency", "monetary", "purchase_freq_annual",
                        "avg_order_value", "predicted_clv"]]
        .sort_values("predicted_clv", ascending=False)
        .reset_index(drop=True)
    )
    st.caption(f"{len(members):,} customers · total predicted CLV {money(members['predicted_clv'].sum())}")
    st.dataframe(members, width="stretch", height=300)
    st.download_button(
        f"Download ({slug}) CSV",
        members.to_csv(index=False).encode(),
        file_name=f"vendrite_{slug}.csv",
        mime="text/csv",
    )
