"""
Signup-month cohort retention (analytical-depth revamp, Phase A).

Groups customers by the calendar month of ``signup_date`` (their cohort), then
for each cohort measures how many of its customers placed at least one order in
the calendar month that is 0, 1, 2, ... months after signup, and UPSERTS the
grid into ``analytics.cohort_retention``.

    cohort_month         first day of the signup month
    months_since_signup  0 = the signup month itself, 1 = the next month, ...
    cohort_size          # customers who signed up that month
    retained_customers   # of those who ordered in (signup month + k)
    retention_rate       retained_customers / cohort_size   (0..1)

Only cells the data can actually observe are emitted: a cohort that signed up
3 months before the most recent order in the warehouse gets rows for k = 0..3,
not k = 0..COHORT_MAX_MONTHS -- otherwise later months would show a fake drop
to zero simply because no data exists for them yet.

Notes / edge behaviour
----------------------
* Orders dated *before* a customer's signup (messy data) map to a negative
  ``months_since_signup`` and are ignored.
* A customer whose only orders predate their signup still counts toward
  ``cohort_size`` but never toward ``retained_customers``.
* Customers with no ``signup_date`` have no cohort and are excluded.

``compute_cohort_retention`` is pure and unit-tested against two hand-checkable
cohorts.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

# --- ensure the project root is importable, however this file is launched ----
# `python -m ...` and pytest put the repo root on sys.path; a bare
# `python analytics/cohorts.py` does not. Add it before first-party imports.
import sys as _sys
from pathlib import Path as _Path

_ROOT = str(_Path(__file__).resolve().parents[1])
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
# ---------------------------------------------------------------------------

from analytics.clv import read_customers
from analytics.segmentation import read_fact_for_rfm
from config import settings
from etl.load import get_engine, log_run, reflect

logger = logging.getLogger(__name__)

RETENTION_COLUMNS = [
    "cohort_month",
    "months_since_signup",
    "cohort_size",
    "retained_customers",
    "retention_rate",
]


# ---------------------------------------------------------------------------
# pure computation
# ---------------------------------------------------------------------------
def _month_index(ts: pd.Series) -> pd.Series:
    """Calendar month as a single integer (year*12 + month-1) for month maths."""
    dt = pd.to_datetime(ts)
    return dt.dt.year * 12 + (dt.dt.month - 1)


def compute_cohort_retention(
    customers_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    *,
    max_months: int,
) -> pd.DataFrame:
    """``customers_df``: [customer_id, signup_date]. ``orders_df``:
    [customer_id, order_date] (one row per order line is fine -- de-duped here).
    Returns the retention grid (see module docstring)."""
    cust = customers_df.dropna(subset=["signup_date"]).copy()
    cust["signup_date"] = pd.to_datetime(cust["signup_date"])
    cust["cohort_month"] = cust["signup_date"].dt.to_period("M").dt.to_timestamp()
    cust["cohort_mi"] = _month_index(cust["signup_date"])

    orders = orders_df.copy()
    orders["order_date"] = pd.to_datetime(orders["order_date"])

    if cust.empty or orders.empty:
        return pd.DataFrame(columns=RETENTION_COLUMNS)

    last_order_mi = int(_month_index(orders["order_date"]).max())

    merged = orders.merge(
        cust[["customer_id", "cohort_month", "cohort_mi"]], on="customer_id", how="inner"
    )
    merged["months_since_signup"] = _month_index(merged["order_date"]) - merged["cohort_mi"]
    merged = merged[
        (merged["months_since_signup"] >= 0) & (merged["months_since_signup"] <= max_months)
    ]

    active = (
        merged.groupby(["cohort_month", "months_since_signup"])["customer_id"]
        .nunique()
        .rename("retained_customers")
        .reset_index()
    )
    cohort_size = cust.groupby("cohort_month")["customer_id"].nunique()

    records: list[dict] = []
    for cohort_month, size in cohort_size.items():
        cohort_mi = cohort_month.year * 12 + (cohort_month.month - 1)
        observable = min(max_months, last_order_mi - cohort_mi)
        if observable < 0:  # cohort signed up after the last data month
            continue
        for k in range(observable + 1):
            hit = active.loc[
                (active["cohort_month"] == cohort_month)
                & (active["months_since_signup"] == k),
                "retained_customers",
            ]
            retained = int(hit.iloc[0]) if len(hit) else 0
            records.append(
                {
                    "cohort_month": cohort_month.date(),
                    "months_since_signup": int(k),
                    "cohort_size": int(size),
                    "retained_customers": retained,
                    "retention_rate": round(retained / int(size), 4),
                }
            )

    return (
        pd.DataFrame.from_records(records, columns=RETENTION_COLUMNS)
        .sort_values(["cohort_month", "months_since_signup"])
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# write (idempotent upsert on the natural cell key)
# ---------------------------------------------------------------------------
def load_cohort_retention(
    engine: Engine, retention_df: pd.DataFrame, computed_date: date
) -> int:
    """UPSERT rows into ``analytics.cohort_retention`` keyed on
    (computed_date, cohort_month, months_since_signup) so a same-day re-run
    refreshes rather than duplicates."""
    md = reflect(engine)
    tbl = md.tables[f"{settings.ANALYTICS_SCHEMA}.cohort_retention"]
    rows = [
        {
            "computed_date": computed_date,
            "cohort_month": row.cohort_month,
            "months_since_signup": int(row.months_since_signup),
            "cohort_size": int(row.cohort_size),
            "retained_customers": int(row.retained_customers),
            "retention_rate": float(row.retention_rate),
        }
        for row in retention_df.itertuples(index=False)
    ]
    if rows:
        with engine.begin() as conn:
            stmt = pg_insert(tbl).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["computed_date", "cohort_month", "months_since_signup"],
                set_={
                    "cohort_size": stmt.excluded.cohort_size,
                    "retained_customers": stmt.excluded.retained_customers,
                    "retention_rate": stmt.excluded.retention_rate,
                },
            )
            conn.execute(stmt)
    logger.info(
        "Upserted %d cohort_retention rows (computed_date=%s)", len(rows), computed_date
    )
    return len(rows)


def _avg_retention_at(retention_df: pd.DataFrame, k: int) -> float | None:
    cells = retention_df.loc[retention_df["months_since_signup"] == k, "retention_rate"]
    return round(float(cells.mean()), 4) if len(cells) else None


def run(engine: Engine | None = None) -> dict:
    """Read customers + orders -> build the retention grid -> upsert. Logs to etl_run_log."""
    engine = engine or get_engine("etl")
    log_run(engine, "STARTED")
    try:
        customers = read_customers(engine)
        orders = read_fact_for_rfm(engine).loc[:, ["customer_id", "order_date"]]
        if orders.empty:
            raise RuntimeError("fact_sales is empty -- run the ETL pipeline first")

        retention = compute_cohort_retention(
            customers, orders, max_months=settings.COHORT_MAX_MONTHS
        )
        n = load_cohort_retention(engine, retention, date.today())

        log_run(engine, "SUCCESS", records_processed=n)
        summary = {
            "cohorts": int(retention["cohort_month"].nunique()) if n else 0,
            "cells": int(n),
            "avg_month1_retention": _avg_retention_at(retention, 1),
            "avg_month3_retention": _avg_retention_at(retention, 3),
        }
        logger.info("Cohort retention summary: %s", summary)
        return summary
    except Exception as exc:
        logger.exception("Cohort retention failed")
        try:
            log_run(engine, "FAILED", error_message=repr(exc))
        finally:
            raise


def main() -> None:  # pragma: no cover - thin CLI wrapper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print("cohort retention complete:", run())


if __name__ == "__main__":
    main()
