"""
Unit tests for etl/clean.py — the transformation logic.

These are the tests the spec calls out as most likely to hide silent bugs.
Everything here is pure (no DB, no filesystem). ``test_transform_messy_input``
is the required end-to-end test with deliberately messy data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etl import clean

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "order_id": "ORD-0001",
    "order_datetime": "2026-03-15 10:00:00",  # a Sunday
    "customer_name": "Ada Lovelace",
    "customer_email": "ada@example.com",
    "region": "North",
    "signup_date": "2025-01-01",
    "product_name": "Widget",
    "category": "Gadgets",
    "unit_price": "10.00",
    "quantity": "2",
    "total_amount": "20.00",
}


def row(**overrides) -> dict:
    r = dict(_DEFAULTS)
    r.update(overrides)
    return r


def frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(clean.BUSINESS_COLUMNS))


def coerced(rows: list[dict]) -> pd.DataFrame:
    """strip -> standardize -> coerce, i.e. the state reconcile/partition expect."""
    df = frame(rows)
    df = clean.strip_whitespace(df)
    df = clean.standardize_text(df)
    return clean.coerce_types(df)


# ---------------------------------------------------------------------------
# individual steps
# ---------------------------------------------------------------------------
def test_strip_whitespace_collapses_internal_runs():
    df = clean.strip_whitespace(frame([row(product_name="  Big   Widget  ")]))
    assert df.loc[0, "product_name"] == "Big Widget"


def test_standardize_text_lowercases_email_and_titlecases_region():
    df = clean.standardize_text(
        clean.strip_whitespace(frame([row(customer_email="  ADA@Example.COM ", region="north west")]))
    )
    assert df.loc[0, "customer_email"] == "ada@example.com"
    assert df.loc[0, "region"] == "North West"


def test_deduplicate_removes_exact_and_same_order_line():
    rows = [
        row(),                       # base
        row(),                       # exact duplicate
        row(quantity="9", total_amount="90.00"),  # same (order_id, product, category)
        row(order_id="ORD-0002"),    # genuinely different order
    ]
    df, removed = clean.deduplicate(clean.standardize_text(clean.strip_whitespace(frame(rows))))
    assert removed == 2
    assert len(df) == 2
    assert set(df["order_id"]) == {"ORD-0001", "ORD-0002"}


def test_coerce_types_unparseable_values_become_nat_nan():
    df = clean.coerce_types(
        frame([row(order_datetime="not a date", unit_price="free", quantity="", total_amount="abc")])
    )
    assert pd.isna(df.loc[0, "order_dt"])
    assert pd.isna(df.loc[0, "unit_price"])
    assert pd.isna(df.loc[0, "quantity"])
    assert pd.isna(df.loc[0, "total_amount"])


def test_impute_missing_region_defaults_to_unknown():
    df = clean.impute_missing(coerced([row(region="")]))
    assert df.loc[0, "region"] == "Unknown"


def test_reconcile_totals_recomputes_blank_total():
    df, n = clean.reconcile_totals(coerced([row(total_amount="", unit_price="10", quantity="3")]))
    assert n == 1
    assert df.loc[0, "total_amount"] == pytest.approx(30.0)


def test_reconcile_totals_fixes_absurd_inconsistent_total():
    # regression: an injected 999999.99 must be corrected, not passed through
    df, n = clean.reconcile_totals(
        coerced([row(total_amount="999999.99", unit_price="10", quantity="2")])
    )
    assert n == 1
    assert df.loc[0, "total_amount"] == pytest.approx(20.0)


def test_reconcile_totals_leaves_consistent_total_untouched():
    df, n = clean.reconcile_totals(coerced([row(total_amount="20.00", unit_price="10", quantity="2")]))
    assert n == 0
    assert df.loc[0, "total_amount"] == pytest.approx(20.0)


def test_reconcile_totals_skips_rows_with_invalid_inputs():
    df, n = clean.reconcile_totals(
        coerced([row(quantity="0", unit_price="10", total_amount="12345.00")])
    )
    assert n == 0
    assert df.loc[0, "total_amount"] == pytest.approx(12345.0)  # untouched; quarantined later


@pytest.mark.parametrize(
    "override, reason_substr",
    [
        ({"quantity": "0"}, "non-positive quantity"),
        ({"unit_price": "-5", "total_amount": "-10"}, "non-positive unit_price"),
        ({"product_name": ""}, "missing product_name"),
        ({"order_datetime": "garbage"}, "unparseable order_datetime"),
    ],
)
def test_partition_invalid_flags_bad_rows_with_reasons(override, reason_substr):
    good, bad = clean.partition_invalid(coerced([row(), row(**override)]))
    assert len(good) == 1
    assert len(bad) == 1
    assert reason_substr in bad.loc[0, "quarantine_reason"]
    assert bad.loc[0, "quarantine_stage"] == "transform"


def test_build_dim_customer_dedupes_by_email():
    rows = [
        row(customer_email="x@e.com", customer_name="X One", signup_date="2025-06-01"),
        row(customer_email="x@e.com", customer_name="X Two", signup_date="2024-01-01"),
    ]
    good, _ = clean.partition_invalid(coerced(rows))
    dim = clean.build_dim_customer(good)
    assert list(dim["email"]) == ["x@e.com"]
    assert dim.loc[0, "name"] == "X One"           # first
    assert str(dim.loc[0, "signup_date"]) == "2024-01-01"  # min


def test_build_dim_product_uses_median_price():
    rows = [row(unit_price=p, total_amount=str(float(p) * 2)) for p in ("10", "20", "30")]
    good, _ = clean.partition_invalid(coerced(rows))
    dim = clean.build_dim_product(good)
    assert len(dim) == 1
    assert dim.loc[0, "unit_price"] == pytest.approx(20.0)


def test_build_dim_date_derives_calendar_fields():
    good, _ = clean.partition_invalid(
        coerced([row(order_datetime="2026-03-15 09:00:00"),   # Sunday
                 row(order_id="O2", order_datetime="2026-03-16 09:00:00")])  # Monday
    )
    dim = clean.build_dim_date(good).set_index("date_id")
    assert 20260315 in dim.index and 20260316 in dim.index
    assert bool(dim.loc[20260315, "is_weekend"]) is True
    assert bool(dim.loc[20260316, "is_weekend"]) is False
    assert int(dim.loc[20260315, "quarter"]) == 1


def test_build_fact_sales_types_and_columns():
    good, _ = clean.partition_invalid(coerced([row()]))
    fact = clean.build_fact_sales(good)
    assert list(fact.columns) == [
        "order_id", "customer_email", "product_name", "category",
        "date_id", "quantity", "total_amount",
    ]
    assert fact["quantity"].dtype == np.int64
    assert fact.loc[0, "date_id"] == 20260315


# ---------------------------------------------------------------------------
# required: end-to-end on deliberately messy input
# ---------------------------------------------------------------------------
def test_transform_messy_input():
    rows = [
        row(order_id="G1"),                                             # good
        row(order_id="G2", customer_email="  BOB@Example.COM ", region=" east "),  # good after cleaning
        row(order_id="G1"),                                             # exact duplicate of G1
        row(order_id="G3", total_amount=""),                            # blank total -> reconciled
        row(order_id="G4", total_amount="999999.99"),                   # absurd total -> reconciled
        row(order_id="B1", quantity="0"),                               # invalid -> quarantine
        row(order_id="B2", unit_price="-3", total_amount="-6"),         # invalid -> quarantine
        row(order_id="B3", order_datetime="never"),                     # invalid -> quarantine
        row(order_id="B4", product_name=""),                            # invalid -> quarantine
        row(order_id="G5", region="", customer_email="carol@example.com"),  # good; region -> Unknown
    ]
    result = clean.transform(frame(rows))

    # dims / facts are clean
    assert result.fact_sales["total_amount"].gt(0).all()
    assert result.fact_sales["quantity"].gt(0).all()
    assert result.dim_customer["email"].str.islower().all()
    assert not result.dim_customer["email"].duplicated().any()
    assert "Unknown" in set(result.dim_customer["region"])

    # every reconciled total now equals unit_price * quantity (10 * 2)
    recon = result.fact_sales[result.fact_sales["order_id"].isin(["G3", "G4"])]
    assert [float(v) for v in recon["total_amount"]] == pytest.approx([20.0, 20.0])

    # quarantine captured exactly the four bad orders, each with a reason
    assert set(result.quarantined["order_id"]) == {"B1", "B2", "B3", "B4"}
    assert result.quarantined["quarantine_reason"].str.len().gt(0).all()
    assert (result.quarantined["quarantine_stage"] == "transform").all()

    # good orders survive (G1 deduped once) -> 6 distinct good order_ids
    assert set(result.fact_sales["order_id"]) == {"G1", "G2", "G3", "G4", "G5"}

    # stats line up
    assert result.stats["input_rows"] == 10
    assert result.stats["duplicate_rows_removed"] == 1
    assert result.stats["totals_reconciled"] == 2
    assert result.stats["quarantined_rows"] == 4
    assert result.stats["clean_fact_rows"] == 5
