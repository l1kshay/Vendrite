"""
Unit tests for analytics/forecasting.py — pure time-series + model helpers.
(The spec requires tests for clean.py and segmentation.py; these are extra
coverage for the forecasting math.)
"""

from __future__ import annotations

import pandas as pd
import pytest

from analytics import forecasting as fc


def test_build_daily_series_fills_gaps_with_zero():
    daily = pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-01", "2026-01-04"]), "revenue": [100.0, 400.0]}
    )
    out = fc.build_daily_series(daily)
    assert len(out) == 4
    assert list(out["date"].dt.day) == [1, 2, 3, 4]
    assert out.loc[out["date"].dt.day.isin([2, 3]), "revenue"].tolist() == [0.0, 0.0]


def test_make_features_shape_and_trend_column():
    dates = pd.date_range("2026-01-05", periods=10, freq="D")  # starts Monday
    feats = fc.make_features(dates, origin=dates[0])
    assert list(feats.columns) == fc.FEATURE_COLUMNS
    assert list(feats["t"]) == list(range(10))
    # Monday is the baseline -> all dow one-hots 0 on the first row
    assert feats.iloc[0][[c for c in fc.FEATURE_COLUMNS if c.startswith("dow_")]].sum() == 0
    # every other row has at most one weekday flag set
    assert feats[[c for c in fc.FEATURE_COLUMNS if c.startswith("dow_")]].sum(axis=1).max() == 1


def test_fit_and_forecast_recovers_linear_trend():
    dates = pd.date_range("2026-01-01", periods=40, freq="D")
    series = pd.DataFrame({"date": dates, "revenue": [10.0 * t + 100.0 for t in range(40)]})
    model, origin = fc.fit_model(series)

    trend = model.coef_[fc.FEATURE_COLUMNS.index("t")]
    assert trend == pytest.approx(10.0, abs=0.5)

    preds = fc.forecast(model, origin, series["date"].max(), horizon_days=5)
    assert list(preds.columns) == ["forecast_date", "predicted_sales"]
    assert len(preds) == 5
    assert preds["predicted_sales"].is_monotonic_increasing
    assert preds["predicted_sales"].iloc[0] == pytest.approx(10.0 * 40 + 100.0, abs=5.0)


def test_forecast_clips_negative_predictions_at_zero():
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    series = pd.DataFrame({"date": dates, "revenue": [max(0.0, 300.0 - 10.0 * t) for t in range(30)]})
    model, origin = fc.fit_model(series)
    preds = fc.forecast(model, origin, series["date"].max(), horizon_days=20)
    assert (preds["predicted_sales"] >= 0).all()
