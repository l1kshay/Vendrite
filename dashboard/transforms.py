"""
Pure DataFrame transforms for the dashboard views.

No Streamlit, no I/O -- these take frames from ``dashboard.data`` and reshape
them for a specific chart or table. Kept separate so the interesting logic
(RFM x CLV quadrants, the cohort matrix) is unit-tested without a browser or a
database.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dashboard.theme import QUADRANT_ORDER


# ---------------------------------------------------------------------------
# RFM x CLV
# ---------------------------------------------------------------------------
def rfm_score(segments: pd.DataFrame) -> pd.Series:
    """A 0..1 composite RFM score: the mean of the three percentile ranks, with
    recency inverted (more recent -> higher). Ties share a rank."""
    r = 1.0 - segments["recency"].rank(pct=True)
    f = segments["frequency"].rank(pct=True)
    m = segments["monetary"].rank(pct=True)
    return ((r + f + m) / 3.0).astype(float)


def rfm_clv_frame(segments: pd.DataFrame, clv: pd.DataFrame) -> pd.DataFrame:
    """Inner-join the latest RFM and CLV rows per customer and attach
    ``rfm_score``. Customers missing from either side are dropped."""
    seg = segments.copy()
    seg["rfm_score"] = rfm_score(seg)
    keep_seg = ["customer_id", "customer", "region", "segment_label",
                "recency", "frequency", "monetary", "rfm_score"]
    keep_clv = ["customer_id", "avg_order_value", "purchase_freq_annual",
                "avg_lifespan_years", "predicted_clv"]
    return seg[keep_seg].merge(clv[keep_clv], on="customer_id", how="inner")


def assign_quadrants(
    df: pd.DataFrame,
    *,
    rfm_col: str = "rfm_score",
    clv_col: str = "predicted_clv",
    rfm_split: float | None = None,
    clv_split: float | None = None,
) -> pd.DataFrame:
    """Add a ``quadrant`` column (one of ``theme.QUADRANT_ORDER``) by splitting
    each axis at its median (or an explicit threshold). 'High' means
    strictly-greater-than the split, so a degenerate all-equal axis lands
    everyone in the 'low' half."""
    out = df.copy()
    r_cut = out[rfm_col].median() if rfm_split is None else rfm_split
    c_cut = out[clv_col].median() if clv_split is None else clv_split
    hi_r = out[rfm_col] > r_cut
    hi_c = out[clv_col] > c_cut
    out["quadrant"] = np.select(
        [hi_r & hi_c, ~hi_r & hi_c, hi_r & ~hi_c],
        [QUADRANT_ORDER[0], QUADRANT_ORDER[1], QUADRANT_ORDER[2]],
        default=QUADRANT_ORDER[3],
    )
    out["quadrant"] = pd.Categorical(out["quadrant"], categories=QUADRANT_ORDER, ordered=True)
    return out


def quadrant_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per-quadrant customer count, total and mean CLV -- for the summary table."""
    g = (
        df.groupby("quadrant", observed=False)
        .agg(customers=("customer_id", "size"),
             total_clv=("predicted_clv", "sum"),
             median_clv=("predicted_clv", "median"))
        .reindex(QUADRANT_ORDER)
        .fillna(0)
    )
    g["customers"] = g["customers"].astype(int)
    return g.round({"total_clv": 0, "median_clv": 0})


# ---------------------------------------------------------------------------
# cohort retention
# ---------------------------------------------------------------------------
def retention_matrix(cohorts: pd.DataFrame) -> pd.DataFrame:
    """Wide grid: rows = cohort_month (as date), columns = months_since_signup,
    values = retention_rate. Missing cells stay NaN (cohort not observable that
    far out)."""
    if cohorts.empty:
        return pd.DataFrame()
    m = cohorts.pivot_table(
        index="cohort_month", columns="months_since_signup", values="retention_rate"
    )
    m.index = pd.to_datetime(m.index).date
    m.columns = [int(c) for c in m.columns]
    return m.sort_index()


def retention_counts_matrix(cohorts: pd.DataFrame) -> pd.DataFrame:
    """Wide grid of ``retained_customers``, aligned to :func:`retention_matrix`
    (same index and columns). Feeds the heatmap hover so a cell can show the raw
    counts behind its colour, not just the percentage."""
    if cohorts.empty:
        return pd.DataFrame()
    m = cohorts.pivot_table(
        index="cohort_month", columns="months_since_signup", values="retained_customers"
    )
    m.index = pd.to_datetime(m.index).date
    m.columns = [int(c) for c in m.columns]
    return m.sort_index()


def cohort_sizes(cohorts: pd.DataFrame) -> pd.Series:
    """One cohort_size per cohort_month, indexed by date."""
    if cohorts.empty:
        return pd.Series(dtype="int64")
    s = cohorts.groupby("cohort_month")["cohort_size"].first()
    s.index = pd.to_datetime(s.index).date
    return s.sort_index().astype(int)


def avg_retention_curve(cohorts: pd.DataFrame) -> pd.DataFrame:
    """Mean retention_rate at each months_since_signup, averaged across the
    cohorts that reach that far. Returns [months_since_signup, retention_rate,
    n_cohorts]."""
    if cohorts.empty:
        return pd.DataFrame(columns=["months_since_signup", "retention_rate", "n_cohorts"])
    g = (
        cohorts.groupby("months_since_signup")
        .agg(retention_rate=("retention_rate", "mean"), n_cohorts=("cohort_month", "nunique"))
        .reset_index()
        .sort_values("months_since_signup")
    )
    g["retention_rate"] = g["retention_rate"].round(4)
    return g.reset_index(drop=True)
