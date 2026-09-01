"""
Unit tests for analytics/clv.py -- the heuristic CLV logic.

Pure functions only (no DB). ``test_compute_clv_drops_missing_signup`` and
``test_compute_clv_signup_after_snapshot`` are the deliberately-messy cases:
customers with no signup date, a zero-frequency row, and a signup date that
falls *after* the snapshot must all be handled without NaNs or crashes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics import clv

SNAPSHOT = pd.Timestamp("2026-04-01")
YEAR = 365.25


# ---------------------------------------------------------------------------
# annualised_frequency
# ---------------------------------------------------------------------------
def test_annualised_frequency_uses_365_25():
    f = clv.annualised_frequency(pd.Series([10.0]), pd.Series([365.0]), min_tenure_days=30)
    assert f.iloc[0] == pytest.approx(10.0 / (365.0 / YEAR))


def test_annualised_frequency_floors_short_tenure():
    # 5-day-old customer -> tenure treated as the 30-day floor, not 5 days.
    f = clv.annualised_frequency(pd.Series([1.0]), pd.Series([5]), min_tenure_days=30)
    assert f.iloc[0] == pytest.approx(YEAR / 30)


def test_annualised_frequency_negative_tenure_floored():
    # MESSY: signup recorded after the snapshot -> negative tenure -> floored.
    f = clv.annualised_frequency(pd.Series([2.0]), pd.Series([-10]), min_tenure_days=30)
    assert f.iloc[0] == pytest.approx(2 * YEAR / 30)


# ---------------------------------------------------------------------------
# base_churn_rate / estimate_lifespan_years
# ---------------------------------------------------------------------------
def test_base_churn_rate():
    assert clv.base_churn_rate(pd.Series([10, 20, 200, 300]), churn_days=90) == pytest.approx(0.5)
    assert clv.base_churn_rate(pd.Series([1, 2, 3]), churn_days=90) == 0.0
    assert clv.base_churn_rate(pd.Series([], dtype=float), churn_days=90) == 0.0


def test_estimate_lifespan_one_over_churn_rate():
    # 2 of 4 customers churned (recency > 90) -> churn_rate 0.5 -> lifespan 2.0y
    rec = pd.Series([10, 20, 200, 300])
    assert clv.estimate_lifespan_years(rec, churn_days=90, bounds=(1.0, 10.0)) == pytest.approx(2.0)


def test_estimate_lifespan_no_churn_returns_upper_bound():
    rec = pd.Series([1, 2, 3])
    assert clv.estimate_lifespan_years(rec, churn_days=90, bounds=(1.0, 10.0)) == 10.0


def test_estimate_lifespan_clamped_both_ends():
    # everyone churned -> 1/1.0 = 1.0y, clamped up to the 2.0 lower bound
    allchurn = pd.Series([200, 300, 400])
    assert clv.estimate_lifespan_years(allchurn, churn_days=90, bounds=(2.0, 10.0)) == 2.0
    # 1% churned -> 100y, clamped down to the 10.0 upper bound
    rare = pd.Series([200] + [1] * 99)
    assert clv.estimate_lifespan_years(rare, churn_days=90, bounds=(1.0, 10.0)) == 10.0


# ---------------------------------------------------------------------------
# observed_tenure_days
# ---------------------------------------------------------------------------
def _orders(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["customer_id", "order_date"])
    df["order_date"] = pd.to_datetime(df["order_date"])
    return df


def _customers(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["customer_id", "signup_date"])
    df["signup_date"] = pd.to_datetime(df["signup_date"])
    return df


def test_observed_tenure_days_takes_the_longest():
    orders = _orders([
        (1, "2026-01-01"), (1, "2026-06-30"),   # order span 180 days
        (2, "2026-03-20"), (2, "2026-03-30"),   # order span 10 days
        (3, "2026-08-20"),                       # single order, span 0
    ])
    customers = _customers([
        (1, SNAPSHOT - pd.Timedelta(days=123)),  # signup tenure < order span
        (2, SNAPSHOT - pd.Timedelta(days=730)),  # signup tenure > order span
        (3, None),                                # no signup -> falls to floor
    ])
    t = clv.observed_tenure_days(
        orders, customers, SNAPSHOT, min_tenure_days=30
    ).set_index("customer_id")["tenure_days"]
    assert t.loc[1] == 180        # order span wins
    assert t.loc[2] == 730        # signup tenure wins
    assert t.loc[3] == 30         # min-tenure floor wins


def test_observed_tenure_days_signup_after_snapshot():
    # MESSY: signup dated after the snapshot -> negative signup tenure, ignored.
    orders = _orders([(1, "2026-02-01"), (1, "2026-05-01")])   # span ~89 days
    customers = _customers([(1, SNAPSHOT + pd.Timedelta(days=10))])
    t = clv.observed_tenure_days(orders, customers, SNAPSHOT, min_tenure_days=30)
    assert t.loc[0, "tenure_days"] == pytest.approx(89.0)
    assert np.isfinite(t["tenure_days"]).all()


# ---------------------------------------------------------------------------
# compute_clv -- end to end, hand-checked
# ---------------------------------------------------------------------------
def _rfm(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["customer_id", "recency", "frequency", "monetary"])


def _tenure(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["customer_id", "tenure_days"]).astype(
        {"tenure_days": "float64"}
    )


def test_compute_clv_known_customers():
    rfm = _rfm([
        (1, 10, 4, 1000.0),    # active, 4 orders, $1000 lifetime
        (2, 200, 2, 300.0),    # churned, 2 orders, $300 lifetime
    ])
    tenure = _tenure([(1, 100), (2, 300)])
    out = clv.compute_clv(
        rfm, tenure,
        gross_margin=0.30, churn_days=90, min_tenure_days=30,
        lifespan_bounds=(1.0, 10.0),
    ).set_index("customer_id")

    # 1 of 2 customers churned -> churn_rate 0.5 -> lifespan 2.0y (both rows)
    assert (out["avg_lifespan_years"] == 2.0).all()

    # customer 1
    assert out.loc[1, "avg_order_value"] == pytest.approx(250.0)
    assert out.loc[1, "purchase_freq_annual"] == pytest.approx(4 * YEAR / 100, abs=1e-4)
    assert out.loc[1, "predicted_clv"] == pytest.approx(250.0 * (4 * YEAR / 100) * 2.0 * 0.30, abs=0.05)

    # customer 2
    assert out.loc[2, "avg_order_value"] == pytest.approx(150.0)
    assert out.loc[2, "predicted_clv"] == pytest.approx(150.0 * (2 * YEAR / 300) * 2.0 * 0.30, abs=0.05)


def test_compute_clv_identity_holds():
    rfm = _rfm([(i, 30, i + 1, 100.0 * (i + 1)) for i in range(1, 8)])
    tenure = _tenure([(i, 60 * i) for i in range(1, 8)])
    out = clv.compute_clv(
        rfm, tenure,
        gross_margin=0.25, churn_days=90, min_tenure_days=30,
        lifespan_bounds=(1.0, 10.0),
    )
    recomputed = (
        out["avg_order_value"]
        * out["purchase_freq_annual"]
        * out["avg_lifespan_years"]
        * out["gross_margin"]
    )
    assert np.allclose(recomputed, out["predicted_clv"], atol=0.02)
    assert (out["gross_margin"] == 0.25).all()


def test_compute_clv_zero_frequency_dropped_missing_tenure_floored():
    # MESSY: c2 has frequency 0 (dropped); c3 has no tenure row (-> floor).
    rfm = _rfm([
        (1, 10, 3, 600.0),
        (2, 10, 0, 0.0),
        (3, 10, 2, 400.0),
    ])
    tenure = _tenure([(1, 90)])  # nothing for 2 or 3
    out = clv.compute_clv(
        rfm, tenure,
        gross_margin=0.30, churn_days=90, min_tenure_days=30,
        lifespan_bounds=(1.0, 10.0),
    ).set_index("customer_id")
    assert list(out.index) == [1, 3]
    # customer 3 annualised over the 30-day floor
    assert out.loc[3, "purchase_freq_annual"] == pytest.approx(2 * YEAR / 30, abs=1e-4)
    assert np.isfinite(out["predicted_clv"]).all()
