"""
Unit tests for dashboard/transforms.py — the pure reshaping behind the
Segments (RFM × CLV) and Retention pages. No Streamlit, no DB.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dashboard import transforms as tf
from dashboard.theme import QUADRANT_ORDER


# ---------------------------------------------------------------------------
# RFM × CLV
# ---------------------------------------------------------------------------
def _segments(rows):
    df = pd.DataFrame(rows, columns=["customer_id", "customer", "region",
                                     "segment_label", "recency", "frequency", "monetary"])
    return df


def _clv(rows):
    return pd.DataFrame(rows, columns=["customer_id", "avg_order_value",
                                       "purchase_freq_annual", "avg_lifespan_years", "predicted_clv"])


def test_rfm_score_rewards_recent_frequent_high_value():
    seg = _segments([
        (1, "A", "N", "Champion", 2, 20, 9000.0),
        (2, "B", "N", "New", 40, 5, 2000.0),
        (3, "C", "N", "Hibernating", 300, 1, 100.0),
    ])
    s = tf.rfm_score(seg)
    assert (s.between(0, 1)).all()
    assert s.iloc[0] > s.iloc[1] > s.iloc[2]


def test_rfm_clv_frame_inner_joins():
    seg = _segments([
        (1, "A", "N", "Champion", 2, 20, 9000.0),
        (2, "B", "N", "New", 40, 5, 2000.0),
    ])
    clv = _clv([(1, 450.0, 10.0, 10.0, 45000.0)])  # customer 2 absent
    out = tf.rfm_clv_frame(seg, clv)
    assert list(out["customer_id"]) == [1]
    assert {"rfm_score", "predicted_clv", "segment_label"} <= set(out.columns)


def test_assign_quadrants_median_split():
    df = pd.DataFrame({
        "customer_id": [1, 2, 3, 4],
        "rfm_score": [0.9, 0.8, 0.2, 0.1],
        "predicted_clv": [100.0, 10.0, 100.0, 10.0],
    })
    out = tf.assign_quadrants(df)
    # medians: rfm 0.5, clv 55 -> "high" means strictly greater
    got = dict(zip(out["customer_id"], out["quadrant"].astype(str)))
    assert got[1] == QUADRANT_ORDER[0]   # high rfm, high clv -> Protect
    assert got[2] == QUADRANT_ORDER[2]   # high rfm, low clv  -> Upsell
    assert got[3] == QUADRANT_ORDER[1]   # low rfm, high clv  -> Win back
    assert got[4] == QUADRANT_ORDER[3]   # low, low           -> Low priority


def test_assign_quadrants_degenerate_clv_axis():
    df = pd.DataFrame({
        "customer_id": [1, 2, 3],
        "rfm_score": [0.9, 0.5, 0.1],
        "predicted_clv": [500.0, 500.0, 500.0],   # no variation -> nobody "high"
    })
    out = tf.assign_quadrants(df)
    assert set(out["quadrant"].astype(str)) <= {QUADRANT_ORDER[2], QUADRANT_ORDER[3]}


def test_quadrant_summary_covers_all_quadrants():
    df = pd.DataFrame({
        "customer_id": [1, 2, 3],
        "quadrant": pd.Categorical(
            [QUADRANT_ORDER[0], QUADRANT_ORDER[0], QUADRANT_ORDER[3]],
            categories=QUADRANT_ORDER, ordered=True,
        ),
        "predicted_clv": [1000.0, 3000.0, 50.0],
    })
    summ = tf.quadrant_summary(df)
    assert list(summ.index) == QUADRANT_ORDER
    assert summ.loc[QUADRANT_ORDER[0], "customers"] == 2
    assert summ.loc[QUADRANT_ORDER[1], "customers"] == 0     # empty quadrant -> 0
    assert summ.loc[QUADRANT_ORDER[0], "total_clv"] == pytest.approx(4000.0)


# ---------------------------------------------------------------------------
# cohort retention
# ---------------------------------------------------------------------------
def _cohorts(rows):
    df = pd.DataFrame(rows, columns=["cohort_month", "months_since_signup",
                                     "cohort_size", "retained_customers", "retention_rate"])
    df["cohort_month"] = pd.to_datetime(df["cohort_month"])
    return df


def test_retention_matrix_pivots_and_leaves_gaps_nan():
    c = _cohorts([
        ("2025-01-01", 0, 10, 10, 1.0),
        ("2025-01-01", 1, 10, 6, 0.6),
        ("2025-02-01", 0, 8, 7, 0.875),
        # 2025-02 has no month-1 cell -> should be NaN in the matrix
    ])
    m = tf.retention_matrix(c)
    assert list(m.columns) == [0, 1]
    assert m.loc[pd.Timestamp("2025-01-01").date(), 1] == pytest.approx(0.6)
    assert np.isnan(m.loc[pd.Timestamp("2025-02-01").date(), 1])


def test_cohort_sizes_one_per_cohort():
    c = _cohorts([
        ("2025-01-01", 0, 10, 10, 1.0),
        ("2025-01-01", 1, 10, 6, 0.6),
        ("2025-02-01", 0, 8, 7, 0.875),
    ])
    s = tf.cohort_sizes(c)
    assert s.to_dict() == {pd.Timestamp("2025-01-01").date(): 10,
                           pd.Timestamp("2025-02-01").date(): 8}


def test_avg_retention_curve_means_across_cohorts():
    c = _cohorts([
        ("2025-01-01", 0, 10, 10, 1.0),
        ("2025-02-01", 0, 10, 8, 0.8),
        ("2025-01-01", 1, 10, 5, 0.5),   # only Jan reaches month 1
    ])
    curve = tf.avg_retention_curve(c).set_index("months_since_signup")
    assert curve.loc[0, "retention_rate"] == pytest.approx(0.9)   # mean(1.0, 0.8)
    assert curve.loc[0, "n_cohorts"] == 2
    assert curve.loc[1, "retention_rate"] == pytest.approx(0.5)
    assert curve.loc[1, "n_cohorts"] == 1


def test_empty_inputs_return_empty():
    assert tf.retention_matrix(_cohorts([]).iloc[0:0]).empty
    assert tf.avg_retention_curve(_cohorts([]).iloc[0:0]).empty
