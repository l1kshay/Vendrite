"""
Extraction + ingestion validation (Phase 1).

Responsibilities (and ONLY these):
  1. Read the raw transactions CSV as text.
  2. Validate the *schema* (required columns present).
  3. Validate each row's *format* (parseable dates/numbers, present identifiers,
     email-shaped address).
  4. Split rows into ``valid`` (structurally sound -> eligible for staging) and
     ``quarantined`` (malformed -> excluded from staging, logged, written to
     data/processed/quarantine_extract.csv with a reason). Nothing is dropped
     silently.

Semantic cleaning (dedupe, imputation, type coercion, star mapping, filtering
of quantity<=0 etc.) is NOT done here -- that is etl/clean.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pandas as pd

# --- ensure the project root is importable, however this file is launched ----
# `python -m etl.extract` and pytest put the repo root on sys.path; a bare
# `python etl/extract.py` does not. Add it before the first-party import.
import sys as _sys
from pathlib import Path as _Path

_ROOT = str(_Path(__file__).resolve().parents[1])
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
# ---------------------------------------------------------------------------

from config import settings

logger = logging.getLogger(__name__)

# The source contract: the columns every raw transactions file must provide,
# in canonical order. The mock generator conforms to this list.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "order_id",
    "order_datetime",
    "customer_name",
    "customer_email",
    "region",
    "signup_date",
    "product_name",
    "category",
    "unit_price",
    "quantity",
    "total_amount",
)

# Deliberately permissive: "looks like an address", not RFC 5322.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Fields that must be present for a row to be worth staging at all.
_REQUIRED_NON_EMPTY = ("order_id", "customer_email", "product_name", "order_datetime")


class SchemaError(RuntimeError):
    """Raised when the raw file is missing required columns."""


@dataclass
class ExtractResult:
    valid: pd.DataFrame
    quarantined: pd.DataFrame

    @property
    def n_valid(self) -> int:
        return len(self.valid)

    @property
    def n_quarantined(self) -> int:
        return len(self.quarantined)


def read_raw_csv(path=None) -> pd.DataFrame:
    """Read the raw CSV as all-strings, empty cells as ``""`` (not NaN)."""
    path = settings.RAW_TRANSACTIONS_CSV if path is None else path
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False)
    logger.info("Read %d raw rows from %s", len(df), path)
    return df


def validate_schema(df: pd.DataFrame) -> None:
    """Raise :class:`SchemaError` if any required column is absent."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SchemaError(f"Raw file is missing required column(s): {missing}")
    extra = [c for c in df.columns if c not in REQUIRED_COLUMNS]
    if extra:
        logger.warning("Raw file has unexpected extra column(s), ignoring: %s", extra)


def _row_reasons(row: pd.Series) -> list[str]:
    """Return a list of format-validation failures for one row (empty == OK)."""
    reasons: list[str] = []

    for col in _REQUIRED_NON_EMPTY:
        if str(row[col]).strip() == "":
            reasons.append(f"missing {col}")

    email = str(row["customer_email"]).strip()
    if email and not _EMAIL_RE.match(email):
        reasons.append("malformed email")

    if str(row["order_datetime"]).strip():
        if pd.isna(pd.to_datetime(row["order_datetime"], errors="coerce", format="mixed")):
            reasons.append("unparseable order_datetime")

    qty = str(row["quantity"]).strip()
    if qty == "" or pd.isna(pd.to_numeric(qty, errors="coerce")):
        reasons.append("non-numeric quantity")

    price = str(row["unit_price"]).strip()
    if price == "" or pd.isna(pd.to_numeric(price, errors="coerce")):
        reasons.append("non-numeric unit_price")

    # total_amount MAY be blank (clean.py recomputes it); only a present-but-
    # non-numeric value is a format failure here.
    total = str(row["total_amount"]).strip()
    if total != "" and pd.isna(pd.to_numeric(total, errors="coerce")):
        reasons.append("non-numeric total_amount")

    return reasons


def validate_records(df: pd.DataFrame) -> ExtractResult:
    """Partition ``df`` into valid vs quarantined (with a ``quarantine_reason``)."""
    reasons = df.apply(_row_reasons, axis=1)
    is_bad = reasons.map(bool)

    quarantined = df[is_bad].copy()
    quarantined["quarantine_reason"] = reasons[is_bad].map("; ".join)
    quarantined["quarantine_stage"] = "extract"

    valid = df[~is_bad].copy()

    logger.info(
        "Format validation: %d valid, %d quarantined", len(valid), len(quarantined)
    )
    if len(quarantined):
        top = quarantined["quarantine_reason"].value_counts().head(10)
        logger.info("Top quarantine reasons:\n%s", top.to_string())
    return ExtractResult(valid=valid, quarantined=quarantined)


def write_quarantine(df: pd.DataFrame, path=None) -> None:
    """Persist quarantined rows for auditing (never silently discarded)."""
    path = settings.QUARANTINE_EXTRACT_CSV if path is None else path
    settings.ensure_dirs()
    df.to_csv(path, index=False)
    logger.info("Wrote %d quarantined extract rows -> %s", len(df), path)


def extract(path=None) -> ExtractResult:
    """Full extract stage: read -> schema check -> record validation."""
    df = read_raw_csv(path)
    validate_schema(df)
    result = validate_records(df)
    write_quarantine(result.quarantined)
    return result


def main() -> None:  # pragma: no cover - thin CLI wrapper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    result = extract()
    print(f"valid={result.n_valid}  quarantined={result.n_quarantined}")


if __name__ == "__main__":
    main()
