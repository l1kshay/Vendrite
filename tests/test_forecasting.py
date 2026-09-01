"""
Unit tests for analytics/forecasting.py — pure time-series + model helpers.
(The spec requires tests for clean.py and segmentation.py; these are extra
coverage for the forecasting math, incl. the second model added in the revamp.)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics import forecasting as fc


def _series(revenue) -> pd.DataFrame:
    revenue = list(revenue)
    dates = pd.date_range("2025-01-06", periods=len(revenue), freq="D")  # a Monday
    return pd.DataFrame({"date": dates, "revenue": [float(v) for v in revenue]})


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


# ---------------------------------------------------------------------------
# model 2 — Holt-Winters
# ---------------------------------------------------------------------------
def test_fit_holtwinters_needs_two_full_cycles():
    with pytest.raises(ValueError):
        fc.fit_holtwinters(_series(range(13)), seasonal_periods=7)  # < 14 days


def test_holtwinters_forecast_shape_and_clip():
    # steep downward trend so an unclipped forecast would go negative
    series = _series([max(0.0, 5000.0 - 60.0 * t) for t in range(90)])
    model = fc.fit_holtwinters(series, seasonal_periods=7)
    preds = fc.forecast_holtwinters(model, series["date"].max(), horizon_days=21)
    assert list(preds.columns) == ["forecast_date", "predicted_sales"]
    assert len(preds) == 21
    assert (preds["predicted_sales"] >= 0).all()
    # forecast dates are the contiguous days right after the series
    assert pd.Timestamp(preds["forecast_date"].iloc[0]) == series["date"].max() + pd.Timedelta(days=1)


def test_holtwinters_learns_the_weekly_shape():
    # flat level, strong fixed weekday pattern, tiny noise
    profile = np.array([80, 110, 120, 130, 200, 400, 300], dtype=float)  # Mon..Sun
    rng = np.random.default_rng(0)
    rev = [profile[d % 7] + rng.normal(0, 2) for d in range(84)]
    series = _series(rev)
    model = fc.fit_holtwinters(series, seasonal_periods=7)
    preds = fc.forecast_holtwinters(model, series["date"].max(), horizon_days=7)["predicted_sales"].to_numpy()
    # the 7-day forecast should rank weekdays the same way the profile does
    assert np.argmax(preds) == np.argmax(profile)      # Saturday peak
    assert np.argmin(preds) == np.argmin(profile)      # Monday trough
    assert preds.std() > 50                             # not a flat line


def test_hw_smoothing_params_keys():
    model = fc.fit_holtwinters(_series([100 + i for i in range(60)]), seasonal_periods=7)
    p = fc.hw_smoothing_params(model)
    assert set(p) == {"alpha_level", "beta_trend", "gamma_season"}
    assert all(0.0 <= v <= 1.0 for v in p.values())


# ---------------------------------------------------------------------------
# model comparison — backtest
# ---------------------------------------------------------------------------
def test_model_errors_zero_when_perfect():
    a = np.array([10.0, 20.0, 30.0])
    assert fc.model_errors(a, a) == {"mae": 0.0, "rmse": 0.0, "mape_pct": 0.0}


def test_model_errors_known_values():
    actual = np.array([100.0, 100.0, 100.0, 100.0])
    pred = np.array([110.0, 90.0, 110.0, 90.0])          # abs error 10 each
    e = fc.model_errors(actual, pred)
    assert e["mae"] == pytest.approx(10.0)
    assert e["rmse"] == pytest.approx(10.0)
    assert e["mape_pct"] == pytest.approx(10.0)


def test_backtest_one_row_per_model_with_finite_metrics():
    series = _series([1000 + 3 * t + 50 * np.sin(2 * np.pi * t / 7) for t in range(140)])
    bt = fc.backtest(series, horizon_days=14, seasonal_periods=7)
    assert list(bt["model"]) == [
        fc.settings.FORECAST_MODEL_VERSION,
        fc.settings.FORECAST_HW_MODEL_VERSION,
    ]
    assert (bt["n_holdout"] == 14).all()
    for col in ("mae", "rmse", "mape_pct"):
        assert np.isfinite(bt[col]).all()


def test_backtest_holtwinters_wins_when_level_shifts():
    # level jumps partway through: linreg fits one global slope and lags the
    # shift; Holt-Winters re-levels, so it should score a lower holdout MAE.
    rng = np.random.default_rng(1)
    rev = []
    for t in range(160):
        level = 1000.0 if t < 90 else 2200.0
        rev.append(level + 40 * np.sin(2 * np.pi * t / 7) + rng.normal(0, 15))
    bt = fc.backtest(_series(rev), horizon_days=14, seasonal_periods=7).set_index("model")
    assert bt.loc[fc.settings.FORECAST_HW_MODEL_VERSION, "mae"] < bt.loc[fc.settings.FORECAST_MODEL_VERSION, "mae"]


def test_backtest_raises_when_history_too_short():
    with pytest.raises(ValueError):
        fc.backtest(_series(range(20)), horizon_days=14, seasonal_periods=7)
