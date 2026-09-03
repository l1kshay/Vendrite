"""
Unit tests for analytics/segmentation.py — the RFM logic.

Pure functions only (no DB). ``test_compute_rfm_multiline_orders`` is the
deliberately-messy case: an order that spans several fact rows must count as
ONE order for frequency.
"""

from __future__ import annotations

import pandas as pd
import pytest

from analytics import segmentation as seg

SNAPSHOT = pd.Timestamp("2026-04-01")


def fact(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["customer_id", "order_id", "order_date", "total_amount"])
    df["order_date"] = pd.to_datetime(df["order_date"])
    df["total_amount"] = df["total_amount"].astype(float)
    return df


# ---------------------------------------------------------------------------
# compute_rfm
# ---------------------------------------------------------------------------
def test_compute_rfm_basic_values():
    df = fact([
        (1, "A1", "2026-01-01", 100.0),
        (1, "A2", "2026-02-15", 200.0),
        (1, "A3", "2026-03-30", 300.0),   # last purchase -> recency 2
        (2, "B1", "2026-01-05", 50.0),    # recency 86, freq 1
    ])
    rfm = seg.compute_rfm(df, SNAPSHOT).set_index("customer_id")

    assert rfm.loc[1, "recency"] == 2
    assert rfm.loc[1, "frequency"] == 3
    assert rfm.loc[1, "monetary"] == pytest.approx(600.0)
    assert rfm.loc[2, "recency"] == 86
    assert rfm.loc[2, "frequency"] == 1
    assert rfm.loc[2, "monetary"] == pytest.approx(50.0)


def test_compute_rfm_multiline_orders():
    """MESSY INPUT: a multi-line order appears as repeated rows sharing order_id
    -> frequency must count distinct orders, monetary must sum every line."""
    df = fact([
        (3, "C1", "2026-03-01", 150.0),
        (3, "C1", "2026-03-01", 250.0),   # same order, second line
        (3, "C2", "2026-03-20", 100.0),
    ])
    rfm = seg.compute_rfm(df, SNAPSHOT).set_index("customer_id")
    assert rfm.loc[3, "frequency"] == 2                 # C1 + C2, not 3 rows
    assert rfm.loc[3, "monetary"] == pytest.approx(500.0)
    assert rfm.loc[3, "recency"] == 12                  # 2026-04-01 - 2026-03-20


def test_compute_rfm_window_filters_old_orders():
    df = fact([
        (1, "A1", "2026-01-01", 100.0),   # outside 60-day window
        (1, "A2", "2026-02-15", 200.0),
        (1, "A3", "2026-03-30", 300.0),
        (2, "B1", "2026-01-05", 50.0),    # entirely outside window -> customer dropped
    ])
    rfm = seg.compute_rfm(df, SNAPSHOT, window_days=60).set_index("customer_id")
    assert 2 not in rfm.index
    assert rfm.loc[1, "frequency"] == 2
    assert rfm.loc[1, "monetary"] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def test_quintile_direction_and_bounds():
    s = pd.Series(range(1, 11))
    high = seg._quintile(s, higher_is_better=True)
    low = seg._quintile(s, higher_is_better=False)
    assert high.min() == 1 and high.max() == 5
    assert list(high) == sorted(high)          # larger value -> larger score
    assert high.iloc[0] == low.iloc[-1] and high.iloc[-1] == low.iloc[0]  # mirrored


def test_quintile_small_sample_fallback():
    out = seg._quintile(pd.Series([10.0, 20.0, 30.0]), higher_is_better=True)
    assert out.between(1, 5).all()
    assert list(out) == sorted(out)


def test_score_rfm_rewards_recent_and_frequent():
    rfm = pd.DataFrame({
        "customer_id": [1, 2, 3],
        "recency": [2, 31, 86],      # cust 1 most recent
        "frequency": [10, 5, 1],     # cust 1 most frequent
        "monetary": [900, 400, 50],
    })
    scored = seg.score_rfm(rfm).set_index("customer_id")
    assert scored.loc[1, "r_score"] >= scored.loc[3, "r_score"]
    assert scored.loc[1, "f_score"] >= scored.loc[3, "f_score"]
    assert scored.loc[1, "m_score"] >= scored.loc[3, "m_score"]


# ---------------------------------------------------------------------------
# labelling
# ---------------------------------------------------------------------------
def test_assign_segments_rule_table():
    scored = pd.DataFrame({
        "customer_id": [1, 2, 3, 4, 5, 6],
        "r_score": [5, 3, 5, 2, 1, 3],
        "f_score": [5, 4, 1, 4, 1, 3],
        "m_score": [5, 2, 1, 3, 1, 3],
    })
    labels = list(seg.assign_segments(scored)["segment_label"])
    assert labels == ["Champion", "Loyal", "New", "At Risk", "Hibernating", "Needs Attention"]


def test_assign_segments_labels_are_known():
    scored = pd.DataFrame({
        "customer_id": range(20),
        "r_score": [(i % 5) + 1 for i in range(20)],
        "f_score": [((i * 2) % 5) + 1 for i in range(20)],
        "m_score": [((i * 3) % 5) + 1 for i in range(20)],
    })
    out = seg.assign_segments(scored)
    assert set(out["segment_label"]).issubset(set(seg.SEGMENT_LABELS))


# ---------------------------------------------------------------------------
# end-to-end pure pipeline
# ---------------------------------------------------------------------------
def test_segment_customers_extremes():
    # customer k: k orders, spend k*1000, last order (9-k) days before snapshot
    rows = []
    for k in range(1, 9):
        last = SNAPSHOT - pd.Timedelta(days=9 - k)
        for o in range(k):
            rows.append((k, f"O{k}-{o}", (last - pd.Timedelta(days=o)).strftime("%Y-%m-%d"), 1000.0))
    out = seg.segment_customers(fact(rows), SNAPSHOT).set_index("customer_id")

    assert len(out) == 8
    assert set(out.columns) >= {
        "recency", "frequency", "monetary", "r_score", "f_score", "m_score", "segment_label",
    }
    assert set(out["segment_label"]).issubset(set(seg.SEGMENT_LABELS))
    # customer 8: most recent, most frequent, highest spend
    assert out.loc[8, "segment_label"] == "Champion"
    # customer 1: oldest, single smallest order
    assert out.loc[1, "segment_label"] == "Hibernating"
