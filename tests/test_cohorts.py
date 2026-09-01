"""
Unit tests for analytics/cohorts.py -- signup-month cohort retention.

Pure function only (no DB). ``test_ignores_orders_before_signup`` and
``test_missing_signup_excluded`` are the deliberately-messy cases.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from analytics import cohorts

MAX = 12


def customers(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["customer_id", "signup_date"])
    df["signup_date"] = pd.to_datetime(df["signup_date"])
    return df


def orders(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["customer_id", "order_date"])
    df["order_date"] = pd.to_datetime(df["order_date"])
    return df


def grid(df: pd.DataFrame) -> dict:
    """(cohort_month, months_since_signup) -> (retained, size, rate)."""
    return {
        (r.cohort_month, r.months_since_signup): (
            r.retained_customers, r.cohort_size, r.retention_rate
        )
        for r in df.itertuples(index=False)
    }


# ---------------------------------------------------------------------------
def test_two_cohorts_hand_checked():
    cust = customers([
        (1, "2026-01-10"), (2, "2026-01-20"),   # Jan cohort, size 2
        (3, "2026-02-05"),                        # Feb cohort, size 1
    ])
    ords = orders([
        (1, "2026-01-15"), (1, "2026-02-10"), (1, "2026-03-05"),  # k = 0, 1, 2
        (2, "2026-01-25"),                                          # k = 0 only
        (3, "2026-02-20"), (3, "2026-03-15"),                       # k = 0, 1
    ])
    g = grid(cohorts.compute_cohort_retention(cust, ords, max_months=MAX))

    jan, feb = date(2026, 1, 1), date(2026, 2, 1)
    # Jan cohort is observable for k = 0..2 (last data month = March)
    assert g[(jan, 0)] == (2, 2, 1.0)
    assert g[(jan, 1)] == (1, 2, 0.5)
    assert g[(jan, 2)] == (1, 2, 0.5)
    # Feb cohort observable for k = 0..1
    assert g[(feb, 0)] == (1, 1, 1.0)
    assert g[(feb, 1)] == (1, 1, 1.0)
    assert len(g) == 5


def test_only_data_observable_cells_emitted():
    # max_months allows 12, but data ends the signup month -> only k = 0.
    cust = customers([(1, "2026-03-10"), (2, "2026-03-11")])
    ords = orders([(1, "2026-03-15"), (2, "2026-03-16")])
    out = cohorts.compute_cohort_retention(cust, ords, max_months=MAX)
    assert list(out["months_since_signup"]) == [0]
    assert out.iloc[0]["retention_rate"] == 1.0


def test_ignores_orders_before_signup():
    # MESSY: an order dated two months BEFORE signup must not create a k<0 row.
    cust = customers([(1, "2026-03-01")])
    ords = orders([(1, "2026-01-15"), (1, "2026-03-20")])
    out = cohorts.compute_cohort_retention(cust, ords, max_months=MAX)
    assert len(out) == 1
    row = out.iloc[0]
    assert (row["cohort_month"], row["months_since_signup"]) == (date(2026, 3, 1), 0)
    assert row["retained_customers"] == 1 and row["retention_rate"] == 1.0


def test_drops_cohorts_that_predate_order_history():
    # customer 1 signed up mid-2024 but the order history only starts 2026-01;
    # that cohort's month-0..n retention is unobservable -> the cohort is dropped
    # rather than reported as all-zeros.
    cust = customers([(1, "2024-06-10"), (2, "2026-01-05"), (3, "2026-02-05")])
    ords = orders([
        (1, "2026-01-20"), (1, "2026-02-20"),
        (2, "2026-01-25"),
        (3, "2026-02-10"), (3, "2026-03-10"),
    ])
    out = cohorts.compute_cohort_retention(
        cust, ords, max_months=MAX, signup_grace_months=1
    )
    assert set(out["cohort_month"]) == {date(2026, 1, 1), date(2026, 2, 1)}


def test_missing_signup_excluded():
    # MESSY: customer 1 has no signup_date -> no cohort at all.
    cust = customers([(1, None), (2, "2026-02-01")])
    ords = orders([(1, "2026-02-05"), (2, "2026-02-10")])
    out = cohorts.compute_cohort_retention(cust, ords, max_months=MAX)
    assert out["cohort_month"].nunique() == 1
    assert (out["cohort_size"] == 1).all()


def test_retention_rate_within_bounds():
    cust = customers([(i, f"2026-0{1 + i % 3}-05") for i in range(1, 40)])
    rows = []
    for i in range(1, 40):
        base = 1 + i % 3
        for m in range(0, (i % 4) + 1):
            month = base + m
            rows.append((i, f"2026-{month:02d}-15"))
    out = cohorts.compute_cohort_retention(cust, orders(rows), max_months=MAX)
    assert (out["retention_rate"] >= 0).all() and (out["retention_rate"] <= 1).all()
    assert (out["retained_customers"] <= out["cohort_size"]).all()
    assert (out["months_since_signup"] >= 0).all()


def test_empty_orders_returns_empty_grid():
    out = cohorts.compute_cohort_retention(
        customers([(1, "2026-01-01")]), orders([]), max_months=MAX
    )
    assert list(out.columns) == cohorts.RETENTION_COLUMNS
    assert out.empty
