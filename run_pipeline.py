"""
Full Vendrite pipeline entry point (Phase 4).

Runs, in order:  ETL  ->  RFM segmentation  ->  customer lifetime value  ->
cohort retention  ->  sales forecast  ->  run-summary report. Each stage writes
STARTED/SUCCESS/FAILED rows to ``analytics.etl_run_log``
(the report stage is read-only and only logs to stdout). Any stage failure
propagates, this script exits non-zero, and -- because every stage's ``run``
already recorded a FAILED row -- the failure is visible both in CI and in the DB.

This is the script the scheduled GitHub Actions workflow invokes
(``.github/workflows/pipeline.yml``).

    python run_pipeline.py --generate      # regenerate mock source data first
    python run_pipeline.py --skip-report   # ETL + analytics only
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

logger = logging.getLogger("run_pipeline")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the full Vendrite pipeline.")
    p.add_argument("--generate", action="store_true", help="regenerate the mock source CSV first")
    p.add_argument("--skip-report", action="store_true", help="do not render the summary report")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_arg_parser().parse_args(argv)

    # imported here so a plain `--help` needs no DB / heavy deps
    from analytics import clv, cohorts, forecasting, segmentation
    from etl import run_etl

    started = time.time()
    summary: dict[str, object] = {}
    try:
        logger.info("STEP 1/6  ETL")
        summary["etl"] = run_etl.run_full(generate=args.generate)

        logger.info("STEP 2/6  RFM segmentation")
        summary["segmentation"] = segmentation.run()

        logger.info("STEP 3/6  customer lifetime value")
        summary["clv"] = clv.run()

        logger.info("STEP 4/6  cohort retention")
        summary["cohorts"] = cohorts.run()

        logger.info("STEP 5/6  sales forecast")
        summary["forecast"] = forecasting.run()

        if args.skip_report:
            logger.info("STEP 6/6  report  (skipped)")
        else:
            logger.info("STEP 6/6  run-summary report")
            from reporting import generate_report

            paths = generate_report.generate()
            summary["report"] = [p.name for p in paths]
    except Exception:
        logger.exception("PIPELINE FAILED after %.1fs", time.time() - started)
        raise

    logger.info("PIPELINE OK in %.1fs", time.time() - started)
    print("\n=== pipeline summary ===")
    for stage, value in summary.items():
        print(f"{stage:>13} : {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
