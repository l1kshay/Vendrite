"""
Cleaning & transformation logic (Phase 1).

PURE functions only -- no database, no filesystem, no logging side effects on
import. Everything operates on / returns ``pandas`` objects so it is trivially
unit-testable (see tests/test_clean.py).

Pipeline (``transform``):
    require columns
      -> strip whitespace
      -> standardize text (email lower, region title-case, collapse spaces)
      -> deduplicate (exact + same order line twice)
      -> coerce types (datetime / numeric, unparseable -> NaN)
      -> impute missing (region -> 'Unknown', recompute blank/invalid total)
      -> partition invalid rows into a quarantine frame (with reasons)
      -> build star-schema-shaped frames: dim_customer, dim_product,
         dim_date, fact_sales (natural keys; surrogate ids assigned in load.py)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# The 11 business columns expected on input (staging.raw_transactions minus
# provenance columns). Order matters only for readability.
BUSINESS_COLUMNS: tuple[str, ...] = (
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

_COLLAPSE_WS_COLUMNS = ("customer_name", "region", "product_name", "category")


@dataclass
class TransformResult:
    dim_customer: pd.DataFrame            # email, name, signup_date, region
    dim_product: pd.DataFrame             # name, category, unit_price
    dim_date: pd.DataFrame                # date_id, date, day, month, quarter, year, is_weekend
    fact_sales: pd.DataFrame             # order_id, customer_email, product_name, category, date_id, quantity, total_amount
    quarantined: pd.DataFrame            # invalid rows + quarantine_reason + quarantine_stage
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# individual steps -- each does exactly one thing
# ---------------------------------------------------------------------------
def require_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` narrowed to the business columns; raise if any are missing."""
    missing = [c for c in BUSINESS_COLUMNS if c not in df.columns]
    if missing:
        raise KeyError(f"input is missing required column(s): {missing}")
    return df.loc[:, list(BUSINESS_COLUMNS)].copy()


def strip_whitespace(df: pd.DataFrame) -> pd.DataFrame:
    """Trim leading/trailing whitespace on every column; collapse internal runs
    on the human-text columns."""
    df = df.copy()
    for col in BUSINESS_COLUMNS:
        df[col] = df[col].astype("string").str.strip()
    for col in _COLLAPSE_WS_COLUMNS:
        df[col] = df[col].str.replace(r"\s+", " ", regex=True)
    return df


def standardize_text(df: pd.DataFrame) -> pd.DataFrame:
    """Email -> lowercase; region -> Title Case. (Case/format normalization only.)"""
    df = df.copy()
    df["customer_email"] = df["customer_email"].str.lower()
    df["region"] = df["region"].str.title()
    return df


def deduplicate(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop (a) fully identical rows and (b) the same order line appearing twice
    -- identified by (order_id, product_name, category). Returns (df, n_removed)."""
    before = len(df)
    df = df.drop_duplicates(subset=list(BUSINESS_COLUMNS), keep="first")
    df = df.drop_duplicates(subset=["order_id", "product_name", "category"], keep="first")
    return df.reset_index(drop=True), before - len(df)


def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Add typed columns; unparseable values become NaT/NaN (flagged later)."""
    df = df.copy()
    df["order_dt"] = pd.to_datetime(df["order_datetime"], errors="coerce", format="mixed")
    # keep as datetime64 (NaT for missing) so groupby aggregations work;
    # narrowed to a plain date in build_dim_customer.
    df["signup_dt"] = pd.to_datetime(df["signup_date"], errors="coerce")
    df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["total_amount"] = pd.to_numeric(df["total_amount"], errors="coerce")
    return df


def impute_missing(df: pd.DataFrame) -> pd.DataFrame:
    """Region -> 'Unknown' when absent. (Total reconciliation is a separate
    step; see :func:`reconcile_totals`.)"""
    df = df.copy()
    df["region"] = df["region"].fillna("Unknown").replace({"": "Unknown", "Nan": "Unknown"})
    return df


# total_amount is trusted only when it agrees with quantity * unit_price to
# within this absolute tolerance (the source rounds the product to 2 dp).
_TOTAL_TOLERANCE = 0.01


def reconcile_totals(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Recompute ``total_amount`` as ``unit_price * quantity`` whenever the
    line inputs are valid and the stored total is missing, non-positive, or
    inconsistent with the product (an injected 'absurd total' is caught here,
    not just blanks). Returns (df, n_reconciled). Rows whose *inputs* are
    themselves invalid are left for :func:`partition_invalid` to quarantine."""
    df = df.copy()
    inputs_ok = (df["unit_price"] > 0) & (df["quantity"] > 0)
    expected = (df["unit_price"] * df["quantity"]).round(2)
    inconsistent = (
        df["total_amount"].isna()
        | (df["total_amount"] <= 0)
        | ((df["total_amount"] - expected).abs() > _TOTAL_TOLERANCE)
    )
    fix = inputs_ok & inconsistent
    df.loc[fix, "total_amount"] = expected[fix]
    return df, int(fix.sum())


def _invalid_reasons(df: pd.DataFrame) -> pd.Series:
    """One ``'; '``-joined reason string per row ('' == valid)."""
    parts: list[pd.Series] = []

    def flag(mask: pd.Series, label: str) -> None:
        clean_mask = mask.fillna(False).astype(bool)
        parts.append(clean_mask.map(lambda hit: label if hit else ""))

    flag(df["order_dt"].isna(), "unparseable order_datetime")
    flag(df["customer_email"].isna() | (df["customer_email"] == ""), "missing customer_email")
    flag(df["product_name"].isna() | (df["product_name"] == ""), "missing product_name")
    flag(df["quantity"].isna(), "non-numeric quantity")
    flag(df["quantity"].notna() & (df["quantity"] <= 0), "non-positive quantity")
    flag(df["quantity"].notna() & (df["quantity"] % 1 != 0), "fractional quantity")
    flag(df["unit_price"].isna(), "non-numeric unit_price")
    flag(df["unit_price"].notna() & (df["unit_price"] <= 0), "non-positive unit_price")
    flag(df["total_amount"].isna(), "missing total_amount")
    flag(df["total_amount"].notna() & (df["total_amount"] <= 0), "non-positive total_amount")

    joined = parts[0]
    for p in parts[1:]:
        joined = joined.str.cat(p, sep="|")
    # collapse the "a||b|" noise into "a; b"
    return joined.map(lambda s: "; ".join(tok for tok in s.split("|") if tok))


def partition_invalid(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split into (good, quarantined). Quarantined rows keep a reason string."""
    reasons = _invalid_reasons(df)
    is_bad = reasons.astype(bool) & (reasons != "")
    good = df[~is_bad].copy()
    bad = df[is_bad].copy()
    bad["quarantine_reason"] = reasons[is_bad]
    bad["quarantine_stage"] = "transform"
    return good.reset_index(drop=True), bad.reset_index(drop=True)


# ---------------------------------------------------------------------------
# star-schema builders
# ---------------------------------------------------------------------------
def _date_id(ts: pd.Series) -> pd.Series:
    return (ts.dt.year * 10000 + ts.dt.month * 100 + ts.dt.day).astype("int64")


def build_dim_customer(good: pd.DataFrame) -> pd.DataFrame:
    g = good.sort_values("customer_email").groupby("customer_email", as_index=False)
    dim = g.agg(
        name=("customer_name", "first"),
        signup_date=("signup_dt", "min"),
        region=("region", "first"),
    )
    dim = dim.rename(columns={"customer_email": "email"})
    # datetime64 -> plain date (NaT preserved, becomes NULL on load)
    dim["signup_date"] = pd.to_datetime(dim["signup_date"], errors="coerce").dt.date
    return dim.loc[:, ["email", "name", "signup_date", "region"]].reset_index(drop=True)


def build_dim_product(good: pd.DataFrame) -> pd.DataFrame:
    dim = (
        good.sort_values(["product_name", "category"])
        .groupby(["product_name", "category"], as_index=False)
        .agg(unit_price=("unit_price", "median"))
    )
    dim["unit_price"] = dim["unit_price"].round(2)
    return dim.rename(columns={"product_name": "name"}).reset_index(drop=True)


def build_dim_date(good: pd.DataFrame) -> pd.DataFrame:
    dates = pd.to_datetime(good["order_dt"]).dt.normalize().dropna().drop_duplicates()
    dim = pd.DataFrame({"date": dates.sort_values().reset_index(drop=True)})
    dim["date_id"] = _date_id(dim["date"])
    dim["day"] = dim["date"].dt.day
    dim["month"] = dim["date"].dt.month
    dim["quarter"] = dim["date"].dt.quarter
    dim["year"] = dim["date"].dt.year
    dim["is_weekend"] = dim["date"].dt.weekday >= 5
    dim["date"] = dim["date"].dt.date
    return dim.loc[:, ["date_id", "date", "day", "month", "quarter", "year", "is_weekend"]]


def build_fact_sales(good: pd.DataFrame) -> pd.DataFrame:
    fact = pd.DataFrame(
        {
            "order_id": good["order_id"].astype("string"),
            "customer_email": good["customer_email"].astype("string"),
            "product_name": good["product_name"].astype("string"),
            "category": good["category"].astype("string"),
            "date_id": _date_id(pd.to_datetime(good["order_dt"])),
            "quantity": good["quantity"].round().astype("int64"),
            "total_amount": good["total_amount"].round(2),
        }
    )
    return fact.reset_index(drop=True)


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------
def transform(df: pd.DataFrame) -> TransformResult:
    """Run the full clean + star-schema-mapping pipeline on a staging-shaped frame."""
    n_input = len(df)

    df = require_columns(df)
    df = strip_whitespace(df)
    df = standardize_text(df)
    df, n_dupes = deduplicate(df)
    df = coerce_types(df)
    df = impute_missing(df)
    df, n_reconciled = reconcile_totals(df)
    good, quarantined = partition_invalid(df)

    dim_customer = build_dim_customer(good)
    dim_product = build_dim_product(good)
    dim_date = build_dim_date(good)
    fact_sales = build_fact_sales(good)

    stats = {
        "input_rows": n_input,
        "duplicate_rows_removed": int(n_dupes),
        "totals_reconciled": int(n_reconciled),
        "quarantined_rows": int(len(quarantined)),
        "clean_fact_rows": int(len(fact_sales)),
        "dim_customer_rows": int(len(dim_customer)),
        "dim_product_rows": int(len(dim_product)),
        "dim_date_rows": int(len(dim_date)),
    }
    return TransformResult(
        dim_customer=dim_customer,
        dim_product=dim_product,
        dim_date=dim_date,
        fact_sales=fact_sales,
        quarantined=quarantined,
        stats=stats,
    )
