"""
RFM customer segmentation (Phase 2).

Reads ONLY from the ``analytics`` schema (``fact_sales`` x ``dim_date``),
computes Recency / Frequency / Monetary per customer, scores each dimension
into 1-5 quintiles, assigns an explainable segment label, and APPENDS one row
per customer to ``analytics.customer_segments`` (history is never overwritten).

Scoring
-------
* recency   = days between the customer's last purchase and the snapshot date
              (snapshot = latest sale date in fact_sales + 1 day).
              Lower recency -> higher R score.
* frequency = number of distinct orders (order_id).  Higher -> higher F score.
* monetary  = sum of total_amount.                    Higher -> higher M score.

Each score is a 1-5 quintile (``pd.qcut`` on first-rank; a rank-scaled
fallback is used when there are too few customers for 5 clean bins).

Segment rules (first match wins) -- deliberately simple so results can be
hand-checked:

    Champion         R>=4 and F>=4 and M>=4
    Loyal            F>=4 and R>=3
    New              R>=4 and F<=2
    At Risk          R<=2 and F>=3
    Hibernating      R<=2 and F<=2
    Needs Attention  (everything else)

The pure functions (``compute_rfm``, ``score_rfm``, ``assign_segments``,
``segment_customers``) take/return DataFrames and are unit-tested.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

# --- ensure the project root is importable, however this file is launched ----
# `python -m ...` and pytest put the repo root on sys.path; a bare
# `python analytics/segmentation.py` does not. Add it before first-party imports.
import sys as _sys
from pathlib import Path as _Path

_ROOT = str(_Path(__file__).resolve().parents[1])
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
# ---------------------------------------------------------------------------

from config import settings
from etl.load import get_engine, log_run, reflect

logger = logging.getLogger(__name__)

SEGMENT_LABELS = ("Champion", "Loyal", "New", "At Risk", "Hibernating", "Needs Attention")


# ---------------------------------------------------------------------------
# read (analytics schema only)
# ---------------------------------------------------------------------------
def read_fact_for_rfm(engine: Engine) -> pd.DataFrame:
    """Return one row per order line: customer_id, order_id, order_date, total_amount."""
    md = reflect(engine)
    fact = md.tables[f"{settings.ANALYTICS_SCHEMA}.fact_sales"]
    dim_date = md.tables[f"{settings.ANALYTICS_SCHEMA}.dim_date"]
    stmt = select(
        fact.c.customer_id,
        fact.c.order_id,
        dim_date.c.date.label("order_date"),
        fact.c.total_amount,
    ).select_from(fact.join(dim_date, dim_date.c.date_id == fact.c.date_id))
    with engine.connect() as conn:
        df = pd.read_sql(stmt, conn)
    df["order_date"] = pd.to_datetime(df["order_date"])
    df["total_amount"] = pd.to_numeric(df["total_amount"])
    logger.info("Read %d fact rows for RFM", len(df))
    return df


# ---------------------------------------------------------------------------
# pure computation
# ---------------------------------------------------------------------------
def compute_rfm(
    fact_df: pd.DataFrame,
    snapshot_date: pd.Timestamp,
    window_days: int | None = None,
) -> pd.DataFrame:
    """Aggregate fact rows to per-customer recency / frequency / monetary."""
    df = fact_df
    if window_days is not None:
        cutoff = snapshot_date - pd.Timedelta(days=window_days)
        df = df[df["order_date"] >= cutoff]

    grp = df.groupby("customer_id")
    rfm = pd.DataFrame(
        {
            "recency": (snapshot_date - grp["order_date"].max()).dt.days,
            "frequency": grp["order_id"].nunique(),
            "monetary": grp["total_amount"].sum().round(2),
        }
    ).reset_index()
    return rfm


def _quintile(series: pd.Series, *, higher_is_better: bool) -> pd.Series:
    """1-5 score. ``higher_is_better`` -> larger value gets 5 (F, M);
    otherwise smaller value gets 5 (R)."""
    ranks = series.rank(method="first", ascending=higher_is_better)
    n = len(series)
    if n >= 5:
        try:
            return pd.qcut(ranks, 5, labels=[1, 2, 3, 4, 5]).astype(int)
        except ValueError:  # not enough distinct values for 5 bins
            pass
    return np.ceil(ranks / n * 5).clip(1, 5).astype(int)


def score_rfm(rfm: pd.DataFrame) -> pd.DataFrame:
    out = rfm.copy()
    out["r_score"] = _quintile(out["recency"], higher_is_better=False)
    out["f_score"] = _quintile(out["frequency"], higher_is_better=True)
    out["m_score"] = _quintile(out["monetary"], higher_is_better=True)
    return out


def assign_segments(scored: pd.DataFrame) -> pd.DataFrame:
    out = scored.copy()
    r, f, m = out["r_score"], out["f_score"], out["m_score"]
    conditions = [
        (r >= 4) & (f >= 4) & (m >= 4),
        (f >= 4) & (r >= 3),
        (r >= 4) & (f <= 2),
        (r <= 2) & (f >= 3),
        (r <= 2) & (f <= 2),
    ]
    choices = ["Champion", "Loyal", "New", "At Risk", "Hibernating"]
    out["segment_label"] = np.select(conditions, choices, default="Needs Attention")
    return out


def segment_customers(
    fact_df: pd.DataFrame,
    snapshot_date: pd.Timestamp,
    window_days: int | None = None,
) -> pd.DataFrame:
    """Full pure pipeline: fact rows -> per-customer segmented RFM frame."""
    rfm = compute_rfm(fact_df, snapshot_date, window_days)
    scored = score_rfm(rfm)
    labelled = assign_segments(scored)
    return labelled.loc[
        :,
        ["customer_id", "recency", "frequency", "monetary",
         "r_score", "f_score", "m_score", "segment_label"],
    ]


# ---------------------------------------------------------------------------
# write (append-only)
# ---------------------------------------------------------------------------
def load_segments(engine: Engine, segmented: pd.DataFrame, computed_date: date) -> int:
    """APPEND rows to analytics.customer_segments (never overwrite)."""
    md = reflect(engine)
    tbl = md.tables[f"{settings.ANALYTICS_SCHEMA}.customer_segments"]
    rows = [
        {
            "customer_id": int(row.customer_id),
            "recency": int(row.recency),
            "frequency": int(row.frequency),
            "monetary": float(row.monetary),
            "segment_label": str(row.segment_label),
            "computed_date": computed_date,
        }
        for row in segmented.itertuples(index=False)
    ]
    with engine.begin() as conn:
        conn.execute(tbl.insert(), rows)
    logger.info("Appended %d rows to customer_segments (computed_date=%s)", len(rows), computed_date)
    return len(rows)


def run(engine: Engine | None = None, window_days: int | None = None) -> dict:
    """Read fact -> segment -> append to customer_segments. Logs to etl_run_log."""
    engine = engine or get_engine("etl")
    log_run(engine, "STARTED")
    try:
        fact = read_fact_for_rfm(engine)
        if fact.empty:
            raise RuntimeError("fact_sales is empty -- run the ETL pipeline first")
        snapshot = fact["order_date"].max() + pd.Timedelta(days=1)
        segmented = segment_customers(fact, snapshot, window_days)
        n = load_segments(engine, segmented, date.today())

        counts = segmented["segment_label"].value_counts().to_dict()
        log_run(engine, "SUCCESS", records_processed=n)
        logger.info("Segment distribution: %s", counts)
        return {"snapshot_date": snapshot.date().isoformat(), "customers": n, "segments": counts}
    except Exception as exc:
        logger.exception("Segmentation failed")
        try:
            log_run(engine, "FAILED", error_message=repr(exc))
        finally:
            raise


def main() -> None:  # pragma: no cover - thin CLI wrapper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    result = run()
    print("segmentation complete:", result)


if __name__ == "__main__":
    main()
