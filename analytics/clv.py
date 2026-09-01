"""
Customer Lifetime Value -- heuristic and explainable (analytical-depth revamp, Phase A).

Reads ONLY from the ``analytics`` schema, computes a per-customer CLV from first
principles, and APPENDS one row per customer to ``analytics.customer_clv``
(history is never overwritten -- same pattern as ``customer_segments``).

Formula
-------
For customer *i*, over their observed history up to the snapshot date
(snapshot = latest sale in ``fact_sales`` + 1 day):

    CLV_i = AOV_i  x  f_i  x  L  x  m

    AOV_i  average order value = monetary_i / frequency_i
           -- the customer's own mean spend per *distinct* order.

    f_i    annualised purchase frequency
           = frequency_i / (tenure_days_i / 365.25)
           tenure_days_i = (snapshot - signup_date_i), floored at
           CLV_MIN_TENURE_DAYS so a brand-new customer with one order does not
           get an explosive orders-per-year figure.

    L      expected customer lifespan in years, estimated ONCE for the whole
           base as 1 / churn_rate where
               churn_rate = (# customers with recency > CLV_CHURN_DAYS) / N
           clamped to [CLV_LIFESPAN_MIN_YEARS, CLV_LIFESPAN_MAX_YEARS] because a
           ~12-month observation window cannot support estimates outside that
           range. If nobody looks churned, L = the upper bound.

    m      gross margin -- a flat assumption (CLV_GROSS_MARGIN, default 0.30).
           The source has no cost of goods, so CLV is profit-based under this
           single documented constant.

Deliberate simplifications (interview material)
----------------------------------------------
* Historical / heuristic, NOT predictive -- no probabilistic churn model
  (e.g. BG/NBD) and no discounting of future cash flows. Both are natural
  next steps; they are omitted so every term stays hand-checkable.
* Constant margin and a constant future purchase rate are assumed.
* Low-frequency customers have noisy f_i and a noisy global L; the tenure
  floor and the lifespan clamp bound the damage but do not remove it.
* Customers with no ``signup_date`` are dropped (tenure is undefined) and the
  count is logged.

The pure functions (``annualised_frequency``, ``estimate_lifespan_years``,
``compute_clv``) take/return plain frames/arrays and are unit-tested, including
a deliberately-messy case.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

# --- ensure the project root is importable, however this file is launched ----
# `python -m ...` and pytest put the repo root on sys.path; a bare
# `python analytics/clv.py` does not. Add it before first-party imports.
import sys as _sys
from pathlib import Path as _Path

_ROOT = str(_Path(__file__).resolve().parents[1])
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
# ---------------------------------------------------------------------------

from analytics.segmentation import compute_rfm, read_fact_for_rfm
from config import settings
from etl.load import get_engine, log_run, reflect

logger = logging.getLogger(__name__)

CLV_COMPONENT_COLUMNS = (
    "customer_id",
    "avg_order_value",
    "purchase_freq_annual",
    "avg_lifespan_years",
    "gross_margin",
    "predicted_clv",
)


# ---------------------------------------------------------------------------
# read (analytics schema only)
# ---------------------------------------------------------------------------
def read_customers(engine: Engine) -> pd.DataFrame:
    """Return [customer_id, signup_date] from ``dim_customer``."""
    md = reflect(engine)
    dim_customer = md.tables[f"{settings.ANALYTICS_SCHEMA}.dim_customer"]
    stmt = select(dim_customer.c.customer_id, dim_customer.c.signup_date)
    with engine.connect() as conn:
        df = pd.read_sql(stmt, conn)
    df["signup_date"] = pd.to_datetime(df["signup_date"])
    return df


# ---------------------------------------------------------------------------
# pure computation
# ---------------------------------------------------------------------------
def annualised_frequency(
    frequency: pd.Series,
    tenure_days: pd.Series,
    *,
    min_tenure_days: int,
) -> pd.Series:
    """Orders per year over the observed tenure, with a floor on tenure so a
    days-old customer does not divide by a near-zero denominator."""
    tenure = np.maximum(tenure_days.astype("float64"), float(min_tenure_days))
    return frequency.astype("float64") / (tenure / 365.25)


def estimate_lifespan_years(
    recency_days: pd.Series,
    *,
    churn_days: int,
    bounds: tuple[float, float],
) -> float:
    """Global expected lifespan = 1 / churn_rate, clamped to ``bounds``.

    ``churn_rate`` is the share of customers whose most recent purchase is more
    than ``churn_days`` ago. With no churned customers the estimate is
    undefined, so the upper bound is returned.
    """
    lo, hi = bounds
    churn_rate = float((recency_days > churn_days).mean())
    if churn_rate <= 0.0:
        return hi
    return float(np.clip(1.0 / churn_rate, lo, hi))


def compute_clv(
    rfm_df: pd.DataFrame,
    customers_df: pd.DataFrame,
    snapshot_date: pd.Timestamp,
    *,
    gross_margin: float,
    churn_days: int,
    min_tenure_days: int,
    lifespan_bounds: tuple[float, float],
) -> pd.DataFrame:
    """Per-customer CLV plus every component it is built from.

    ``rfm_df``       : [customer_id, recency, frequency, monetary]
                       (straight from :func:`analytics.segmentation.compute_rfm`).
    ``customers_df`` : [customer_id, signup_date].
    """
    df = rfm_df.merge(customers_df, on="customer_id", how="left")

    missing_signup = df["signup_date"].isna()
    if missing_signup.any():
        logger.warning(
            "CLV: dropping %d customer(s) with no signup_date (tenure undefined)",
            int(missing_signup.sum()),
        )
    df = df[~missing_signup & (df["frequency"] > 0)].copy()

    df["avg_order_value"] = (df["monetary"] / df["frequency"]).round(2)

    tenure_days = (snapshot_date - df["signup_date"]).dt.days
    df["purchase_freq_annual"] = annualised_frequency(
        df["frequency"], tenure_days, min_tenure_days=min_tenure_days
    ).round(4)

    lifespan = estimate_lifespan_years(
        df["recency"], churn_days=churn_days, bounds=lifespan_bounds
    )
    df["avg_lifespan_years"] = round(lifespan, 3)
    df["gross_margin"] = float(gross_margin)
    df["predicted_clv"] = (
        df["avg_order_value"]
        * df["purchase_freq_annual"]
        * df["avg_lifespan_years"]
        * df["gross_margin"]
    ).round(2)

    return df.loc[:, list(CLV_COMPONENT_COLUMNS)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# write (append-only)
# ---------------------------------------------------------------------------
def load_clv(
    engine: Engine,
    clv_df: pd.DataFrame,
    computed_date: date,
    method_version: str,
) -> int:
    """APPEND rows to ``analytics.customer_clv`` (never overwrite)."""
    md = reflect(engine)
    tbl = md.tables[f"{settings.ANALYTICS_SCHEMA}.customer_clv"]
    rows = [
        {
            "customer_id": int(row.customer_id),
            "computed_date": computed_date,
            "avg_order_value": float(row.avg_order_value),
            "purchase_freq_annual": float(row.purchase_freq_annual),
            "avg_lifespan_years": float(row.avg_lifespan_years),
            "gross_margin": float(row.gross_margin),
            "predicted_clv": float(row.predicted_clv),
            "method_version": method_version,
        }
        for row in clv_df.itertuples(index=False)
    ]
    with engine.begin() as conn:
        conn.execute(tbl.insert(), rows)
    logger.info(
        "Appended %d rows to customer_clv (computed_date=%s, method=%s)",
        len(rows), computed_date, method_version,
    )
    return len(rows)


def run(engine: Engine | None = None) -> dict:
    """Read fact + customers -> compute CLV -> append to customer_clv. Logs to etl_run_log."""
    engine = engine or get_engine("etl")
    log_run(engine, "STARTED")
    try:
        fact = read_fact_for_rfm(engine)
        if fact.empty:
            raise RuntimeError("fact_sales is empty -- run the ETL pipeline first")
        customers = read_customers(engine)
        snapshot = fact["order_date"].max() + pd.Timedelta(days=1)

        rfm = compute_rfm(fact, snapshot)
        clv = compute_clv(
            rfm,
            customers,
            snapshot,
            gross_margin=settings.CLV_GROSS_MARGIN,
            churn_days=settings.CLV_CHURN_DAYS,
            min_tenure_days=settings.CLV_MIN_TENURE_DAYS,
            lifespan_bounds=(
                settings.CLV_LIFESPAN_MIN_YEARS,
                settings.CLV_LIFESPAN_MAX_YEARS,
            ),
        )
        n = load_clv(engine, clv, date.today(), settings.CLV_METHOD_VERSION)

        log_run(engine, "SUCCESS", records_processed=n)
        summary = {
            "method_version": settings.CLV_METHOD_VERSION,
            "customers": int(n),
            "gross_margin": settings.CLV_GROSS_MARGIN,
            "lifespan_years": float(clv["avg_lifespan_years"].iloc[0]) if n else None,
            "clv_total": round(float(clv["predicted_clv"].sum()), 2),
            "clv_median": round(float(clv["predicted_clv"].median()), 2) if n else None,
        }
        logger.info("CLV summary: %s", summary)
        return summary
    except Exception as exc:
        logger.exception("CLV computation failed")
        try:
            log_run(engine, "FAILED", error_message=repr(exc))
        finally:
            raise


def main() -> None:  # pragma: no cover - thin CLI wrapper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print("clv complete:", run())


if __name__ == "__main__":
    main()
