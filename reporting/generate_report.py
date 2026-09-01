"""
Automated run-summary report (Phase 4).

``generate()`` reads the ``analytics`` schema (+ a single ``staging`` row count
for pipeline health), assembles a metrics dict, renders it through two Jinja2
templates (``summary.md.j2`` and ``summary.html.j2``), and writes timestamped
artifacts plus ``latest.*`` pointers into ``reports/``.

Called at the end of ``run_pipeline.py`` after ETL + analytics have succeeded.
Read-only: it never writes to the database.

    python -m reporting.generate_report
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import text
from sqlalchemy.engine import Engine

# --- ensure the project root is importable, however this file is launched ----
# `python -m reporting.generate_report` and pytest put the repo root on
# sys.path; a bare `python reporting/generate_report.py` does not. Add it here.
import sys as _sys

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
# ---------------------------------------------------------------------------

from config import settings
from etl.load import get_engine

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


# ---------------------------------------------------------------------------
# metric collection
# ---------------------------------------------------------------------------
def _scalar(conn, sql: str, **params):
    return conn.execute(text(sql), params).scalar()


def _quarantine_count(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(len(pd.read_csv(path)))
    except pd.errors.EmptyDataError:
        return 0


def _fmt_ts(value: Any, with_seconds: bool = True) -> str | None:
    """Normalise a timestamp to 'YYYY-MM-DD HH:MM[:SS] UTC' (drops microseconds)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.Timestamp(value)
    ts = ts.tz_convert("UTC") if ts.tzinfo is not None else ts
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC" if with_seconds else "%Y-%m-%d %H:%M UTC")


def _clean_runs(df: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    for row in df.to_dict("records"):
        rec = row["records_processed"]
        out.append(
            {
                "run_id": int(row["run_id"]),
                "run_timestamp": _fmt_ts(row["run_timestamp"]),
                "status": row["status"],
                "records_processed": None if pd.isna(rec) else int(rec),
                "error_message": None if pd.isna(row["error_message"]) else str(row["error_message"]),
            }
        )
    return out


def gather_metrics(engine: Engine) -> dict:
    """Collect everything the templates need. All SQL is parameterised / constant."""
    with engine.connect() as conn:
        last_sale = _scalar(
            conn,
            "SELECT max(d.date) FROM analytics.fact_sales f "
            "JOIN analytics.dim_date d ON d.date_id = f.date_id",
        )
        if last_sale is None:
            raise RuntimeError("fact_sales is empty -- run the ETL before reporting")

        db = {
            "staging_rows": _scalar(conn, "SELECT count(*) FROM staging.raw_transactions"),
            "fact_rows": _scalar(conn, "SELECT count(*) FROM analytics.fact_sales"),
            "customers": _scalar(conn, "SELECT count(*) FROM analytics.dim_customer"),
            "products": _scalar(conn, "SELECT count(*) FROM analytics.dim_product"),
            "first_sale": _scalar(
                conn,
                "SELECT min(d.date) FROM analytics.fact_sales f "
                "JOIN analytics.dim_date d ON d.date_id = f.date_id",
            ),
            "last_sale": last_sale,
        }

        w30 = last_sale - timedelta(days=30)
        w60 = last_sale - timedelta(days=60)
        kpi = {
            "revenue": float(_scalar(conn, "SELECT coalesce(sum(total_amount),0) FROM analytics.fact_sales")),
            "orders": int(_scalar(conn, "SELECT count(DISTINCT order_id) FROM analytics.fact_sales")),
            "units": int(_scalar(conn, "SELECT coalesce(sum(quantity),0) FROM analytics.fact_sales")),
        }
        kpi["aov"] = kpi["revenue"] / kpi["orders"] if kpi["orders"] else 0.0
        rev_window_sql = (
            "SELECT coalesce(sum(f.total_amount),0) FROM analytics.fact_sales f "
            "JOIN analytics.dim_date d ON d.date_id = f.date_id "
            "WHERE d.date > :lo AND d.date <= :hi"
        )
        kpi["rev_30d"] = float(_scalar(conn, rev_window_sql, lo=w30, hi=last_sale))
        kpi["rev_prev_30d"] = float(_scalar(conn, rev_window_sql, lo=w60, hi=w30))
        kpi["rev_change_pct"] = (
            (kpi["rev_30d"] - kpi["rev_prev_30d"]) / kpi["rev_prev_30d"] * 100
            if kpi["rev_prev_30d"]
            else None
        )

        top_categories = pd.read_sql(
            text(
                "SELECT p.category, round(sum(f.total_amount),2) AS revenue, "
                "sum(f.quantity) AS units FROM analytics.fact_sales f "
                "JOIN analytics.dim_product p ON p.product_id = f.product_id "
                "GROUP BY p.category ORDER BY revenue DESC LIMIT 5"
            ),
            conn,
        ).to_dict("records")

        top_products = pd.read_sql(
            text(
                "SELECT p.name AS product, p.category, round(sum(f.total_amount),2) AS revenue, "
                "sum(f.quantity) AS units FROM analytics.fact_sales f "
                "JOIN analytics.dim_product p ON p.product_id = f.product_id "
                "GROUP BY p.name, p.category ORDER BY revenue DESC LIMIT 5"
            ),
            conn,
        ).to_dict("records")

        seg_cohort = _scalar(conn, "SELECT max(computed_date) FROM analytics.customer_segments")
        segments = {"cohort_date": seg_cohort, "rows": []}
        if seg_cohort is not None:
            segments["rows"] = pd.read_sql(
                text(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (customer_id) customer_id, recency, frequency,
                               monetary, segment_label
                        FROM analytics.customer_segments
                        ORDER BY customer_id, computed_date DESC, segment_id DESC
                    )
                    SELECT segment_label AS label, count(*) AS customers,
                           round(avg(recency))       AS avg_recency,
                           round(avg(frequency), 1)  AS avg_frequency,
                           round(avg(monetary), 2)   AS avg_monetary
                    FROM latest GROUP BY segment_label ORDER BY customers DESC
                    """
                ),
                conn,
            ).to_dict("records")

        fc_gen = _scalar(conn, "SELECT max(generated_date) FROM analytics.sales_forecast")
        forecast = None
        if fc_gen is not None:
            row = conn.execute(
                text(
                    """
                    SELECT model_version, count(*) AS n, min(forecast_date) AS first_date,
                           max(forecast_date) AS last_date, round(sum(predicted_sales),2) AS total,
                           round(avg(predicted_sales),2) AS avg_daily
                    FROM analytics.sales_forecast
                    WHERE generated_date = :g GROUP BY model_version
                    """
                ),
                {"g": fc_gen},
            ).mappings().first()
            forecast = dict(row) if row else None
            if forecast:
                forecast["generated_date"] = _fmt_ts(fc_gen, with_seconds=False)

        runs = _clean_runs(
            pd.read_sql(
                text(
                    "SELECT run_id, run_timestamp, status, records_processed, error_message "
                    "FROM analytics.etl_run_log ORDER BY run_id DESC LIMIT 12"
                ),
                conn,
            )
        )

    quality = {
        "extract_quarantined": _quarantine_count(settings.QUARANTINE_EXTRACT_CSV),
        "transform_quarantined": _quarantine_count(settings.QUARANTINE_TRANSFORM_CSV),
    }

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "db": db,
        "kpi": kpi,
        "top_categories": top_categories,
        "top_products": top_products,
        "segments": segments,
        "forecast": forecast,
        "quality": quality,
        "runs": runs,
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["money"] = lambda v: "n/a" if v is None else f"${v:,.2f}"
    env.filters["num"] = lambda v: "n/a" if v is None else f"{v:,.0f}"
    env.filters["pct"] = lambda v: "n/a" if v is None else f"{v:+.1f}%"
    return env


def render(metrics: dict, fmt: str) -> str:
    """fmt = 'md' or 'html'."""
    template = _environment().get_template(f"summary.{fmt}.j2")
    return template.render(**metrics)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def generate(engine: Engine | None = None, out_dir: Path | None = None) -> list[Path]:
    engine = engine or get_engine("etl")
    out_dir = out_dir or settings.REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics = gather_metrics(engine)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    written: list[Path] = []
    for fmt in ("md", "html"):
        content = render(metrics, fmt)
        stamped = out_dir / f"summary_{stamp}.{fmt}"
        latest = out_dir / f"latest.{fmt}"
        stamped.write_text(content, encoding="utf-8")
        latest.write_text(content, encoding="utf-8")
        written.extend([stamped, latest])
        logger.info("wrote %s", stamped)
    return written


def main() -> None:  # pragma: no cover - thin CLI wrapper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    paths = generate()
    print("report artifacts:")
    for p in paths:
        print(" ", p)


if __name__ == "__main__":
    main()
