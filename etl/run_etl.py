"""
ETL orchestrator / CLI (Phase 1).

Wires the stages together in the spec's order:

    (optional) generate mock CSV
      -> extract   : read + validate format, quarantine malformed rows
      -> load       : write valid rows to staging.raw_transactions
      -> transform  : staging -> clean star-schema frames (quarantine invalid)
      -> load       : upsert dimensions, insert facts
      -> log        : SUCCESS / FAILED row in analytics.etl_run_log

Fails loudly: any exception is written to etl_run_log as FAILED and re-raised
with a non-zero exit code (so GitHub Actions marks the run red).

Usage:
    python -m etl.run_etl --generate          # regenerate mock data, then full ETL
    python -m etl.run_etl                      # full ETL against the existing CSV
    python -m etl.run_etl --offline --generate # no DB: run generate+extract+clean,
                                               # write star frames to data/processed/
"""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

# --- ensure the project root is importable, however this file is launched ----
# `python -m etl.run_etl` and pytest put the repo root on sys.path; a bare
# `python etl/run_etl.py` does not. Add it before the first-party imports.
import sys as _sys
from pathlib import Path as _Path

_ROOT = str(_Path(__file__).resolve().parents[1])
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
# ---------------------------------------------------------------------------

from config import settings
from etl import clean, extract
from etl import generate_mock_data as mock

logger = logging.getLogger("etl.run_etl")


def _summary(title: str, mapping: dict) -> str:
    width = max(len(k) for k in mapping)
    lines = [title, "-" * len(title)]
    lines += [f"  {k.ljust(width)} : {v}" for k, v in mapping.items()]
    return "\n".join(lines)


def run_offline(generate: bool) -> dict:
    """DB-free path -- useful for local dev without PostgreSQL and for eyeballing
    the transformation output. Writes star frames + quarantine CSVs to
    data/processed/."""
    settings.ensure_dirs()
    if generate:
        mock.generate()

    ext = extract.extract()
    staging_like = ext.valid  # what WOULD be loaded to staging
    result = clean.transform(staging_like)

    out = settings.DATA_PROCESSED_DIR
    result.dim_customer.to_csv(out / "dim_customer.csv", index=False)
    result.dim_product.to_csv(out / "dim_product.csv", index=False)
    result.dim_date.to_csv(out / "dim_date.csv", index=False)
    result.fact_sales.to_csv(out / "fact_sales.csv", index=False)
    result.quarantined.to_csv(settings.QUARANTINE_TRANSFORM_CSV, index=False)

    stats = {
        "mode": "offline",
        "extract_valid": ext.n_valid,
        "extract_quarantined": ext.n_quarantined,
        **result.stats,
        "processed_dir": str(out),
    }
    print(_summary("ETL (offline) complete", stats))
    return stats


def run_full(generate: bool) -> dict:
    """Full pipeline against PostgreSQL, logging to analytics.etl_run_log."""
    from etl import load  # imported lazily so --offline needs no DB config

    settings.ensure_dirs()
    engine = load.get_engine("etl")

    load.log_run(engine, "STARTED")
    try:
        if generate:
            mock.generate()

        ext = extract.extract()
        source_file = settings.RAW_TRANSACTIONS_CSV.name
        load.load_to_staging(engine, ext.valid, source_file)

        staging_df = load.read_staging(engine)
        result = clean.transform(staging_df)
        result.quarantined.to_csv(settings.QUARANTINE_TRANSFORM_CSV, index=False)

        customer_map = load.upsert_dim_customer(engine, result.dim_customer)
        product_map = load.upsert_dim_product(engine, result.dim_product)
        load.upsert_dim_date(engine, result.dim_date)
        fact_rows = load.insert_fact_sales(
            engine, result.fact_sales, customer_map, product_map
        )

        load.log_run(engine, "SUCCESS", records_processed=fact_rows)
        stats = {
            "mode": "full",
            "extract_valid": ext.n_valid,
            "extract_quarantined": ext.n_quarantined,
            **result.stats,
            "fact_rows_loaded": fact_rows,
        }
        print(_summary("ETL complete", stats))
        return stats
    except Exception as exc:  # fail loudly
        logger.exception("ETL failed")
        try:
            load.log_run(engine, "FAILED", error_message=repr(exc))
        finally:
            raise


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the Vendrite ETL pipeline.")
    p.add_argument("--generate", action="store_true", help="regenerate the mock CSV first")
    p.add_argument(
        "--offline",
        action="store_true",
        help="skip all DB steps; write star frames to data/processed/",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_arg_parser().parse_args(argv)
    if args.offline:
        run_offline(args.generate)
    else:
        run_full(args.generate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
