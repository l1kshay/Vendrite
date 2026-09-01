"""
Mock e-commerce data generator (Phase 1).

There is no real upstream source for Vendrite, so this script synthesizes a
realistic raw transactions CSV: customers, products, and order lines spanning
``VENDRITE_MOCK_MONTHS_BACK`` months, with a configurable fraction of rows
deliberately corrupted so the cleaning stage has genuine work to do.

Output: ``data/raw/transactions_raw.csv`` (one row per order line).
All values are written as strings -- this is a *raw* extract, not clean data.

Determinism: fully seeded via ``VENDRITE_MOCK_SEED`` so runs are reproducible
(important for the hand-check in Phase 2 and for tests).

Run:  python -m etl.generate_mock_data
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

# --- ensure the project root is importable, however this file is launched ----
# `python -m etl.generate_mock_data` and pytest put the repo root on sys.path;
# a bare `python etl/generate_mock_data.py` does not. Add it here.
import sys as _sys

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
# ---------------------------------------------------------------------------

from config import settings
from etl.extract import REQUIRED_COLUMNS

logger = logging.getLogger(__name__)

# The generator conforms to extract.py's source contract.
RAW_COLUMNS: tuple[str, ...] = REQUIRED_COLUMNS

_FIRST_NAMES = (
    "Ava Liam Noah Emma Olivia Sophia Mason Lucas Mia Amelia Ethan Harper "
    "James Ella Benjamin Aria Henry Scarlett Jack Nora Leo Zoe Owen Lily "
    "Ryan Chloe Nathan Layla Isaac Riya Arjun Priya Neha Rohan Sara Kabir"
).split()
_LAST_NAMES = (
    "Smith Johnson Williams Brown Jones Garcia Miller Davis Rodriguez Martinez "
    "Hernandez Lopez Gonzalez Wilson Anderson Thomas Taylor Moore Jackson Martin "
    "Lee Perez Thompson White Harris Sharma Patel Singh Kumar Nair Iyer Bose"
).split()

_PRODUCT_NOUNS = {
    "Electronics": ("Headphones", "Speaker", "Charger", "Webcam", "Router", "SSD", "Monitor", "Keyboard"),
    "Home & Kitchen": ("Blender", "Kettle", "Cookware Set", "Knife Block", "Toaster", "Air Fryer", "Mug Set"),
    "Books": ("Novel", "Cookbook", "Biography", "Textbook", "Journal", "Atlas", "Anthology"),
    "Clothing": ("T-Shirt", "Jeans", "Jacket", "Sneakers", "Hoodie", "Scarf", "Socks Pack"),
    "Sports": ("Yoga Mat", "Dumbbell Set", "Tennis Racket", "Football", "Water Bottle", "Resistance Bands"),
    "Toys": ("Building Blocks", "Puzzle", "Action Figure", "Board Game", "RC Car", "Plush Bear"),
    "Beauty": ("Face Serum", "Lip Balm Set", "Shampoo", "Perfume", "Nail Kit", "Moisturizer"),
}
_PRODUCT_ADJECTIVES = ("Classic", "Pro", "Deluxe", "Everyday", "Compact", "Premium", "Essential", "Ultra")


def _build_customers(rng: np.random.Generator, n: int, months_back: int) -> list[dict]:
    customers: list[dict] = []
    today = date.today()
    # Signups must OVERLAP the order window (orders span the last `months_back`
    # months) or cohort-retention cells for early-signup customers would all be
    # structural zeros. Draw signup 30 days ago .. ~1.5 months before the order
    # window starts: every customer then has time to make a "retained" purchase,
    # while the earliest 1-2 cohorts still predate the first order by <= the
    # analytics COHORT_SIGNUP_GRACE_MONTHS.
    max_offset = int(30.44 * months_back) + 45
    for i in range(1, n + 1):
        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)
        # unique-ish email via customer index
        email = f"{first}.{last}{i}@example.com".lower()
        signup_offset = int(rng.integers(30, max_offset))
        customers.append(
            {
                "customer_name": f"{first} {last}",
                "customer_email": email,
                "region": rng.choice(settings.MOCK_REGIONS),
                "signup_date": (today - timedelta(days=signup_offset)).isoformat(),
            }
        )
    return customers


def _build_products(rng: np.random.Generator, n: int) -> list[dict]:
    products: list[dict] = []
    categories = list(settings.MOCK_CATEGORIES)
    for i in range(n):
        category = categories[i % len(categories)]
        noun = rng.choice(_PRODUCT_NOUNS[category])
        adj = rng.choice(_PRODUCT_ADJECTIVES)
        price = round(float(rng.uniform(5, 400)), 2)
        products.append(
            {
                "product_name": f"{adj} {noun}",
                "category": category,
                "unit_price": price,
            }
        )
    return products


def _random_datetime(rng: np.random.Generator, months_back: int) -> datetime:
    start = datetime.now() - timedelta(days=int(30.44 * months_back))
    span_seconds = int((datetime.now() - start).total_seconds())
    return start + timedelta(seconds=int(rng.integers(0, span_seconds)))


def _messy_variant(row: dict, kind: int, rng: np.random.Generator) -> dict:
    """Return a corrupted copy of ``row``. ``kind`` selects the corruption."""
    r = dict(row)
    if kind == 0:  # blank email
        r["customer_email"] = ""
    elif kind == 1:  # malformed email
        r["customer_email"] = rng.choice(["n/a", "john doe at example", "—", "NULL"])
    elif kind == 2:  # missing product name
        r["product_name"] = ""
    elif kind == 3:  # unparseable datetime
        r["order_datetime"] = rng.choice(["2026-13-40 99:99:99", "not a date", "", "31/02/2026"])
    elif kind == 4:  # invalid quantity
        r["quantity"] = rng.choice(["0", "-3", "two", ""])
    elif kind == 5:  # invalid unit price
        r["unit_price"] = rng.choice(["N/A", "-19.99", "", "free"])
    elif kind == 6:  # blank total -> must be recomputed downstream
        r["total_amount"] = ""
    elif kind == 7:  # absurd total amount (semantic outlier, still numeric)
        r["total_amount"] = "999999.99"
    elif kind == 8:  # whitespace / casing noise on region + email
        r["region"] = f"  {str(row['region']).upper()} "
        r["customer_email"] = f"  {str(row['customer_email']).upper()} "
    elif kind == 9:  # blank / malformed signup date
        r["signup_date"] = rng.choice(["", "not-a-date", "0000-00-00"])
    return r


def generate(
    *,
    seed: int | None = None,
    n_customers: int | None = None,
    n_products: int | None = None,
    n_orders: int | None = None,
    months_back: int | None = None,
    messy_fraction: float | None = None,
    out_path: Path | None = None,
) -> Path:
    """Generate the raw CSV. Returns the output ``Path``.

    All parameters default to the corresponding ``config.settings`` value.
    """
    seed = settings.MOCK_SEED if seed is None else seed
    n_customers = settings.MOCK_N_CUSTOMERS if n_customers is None else n_customers
    n_products = settings.MOCK_N_PRODUCTS if n_products is None else n_products
    n_orders = settings.MOCK_N_ORDERS if n_orders is None else n_orders
    months_back = settings.MOCK_MONTHS_BACK if months_back is None else months_back
    messy_fraction = settings.MOCK_MESSY_FRACTION if messy_fraction is None else messy_fraction
    out_path = settings.RAW_TRANSACTIONS_CSV if out_path is None else out_path

    settings.ensure_dirs()
    rng = np.random.default_rng(seed)

    customers = _build_customers(rng, n_customers, months_back)
    products = _build_products(rng, n_products)
    logger.info("Generated %d customers, %d products", len(customers), len(products))

    rows: list[dict] = []
    order_seq = 0
    while len(rows) < n_orders:
        order_seq += 1
        order_id = f"ORD-{order_seq:06d}"
        customer = customers[int(rng.integers(0, len(customers)))]
        ordered_at = _random_datetime(rng, months_back)
        n_lines = int(rng.integers(1, 4))  # 1..3 products per order
        line_products = rng.choice(len(products), size=n_lines, replace=False)
        for pidx in line_products:
            product = products[int(pidx)]
            quantity = int(rng.integers(1, 6))
            unit_price = product["unit_price"]
            total = round(unit_price * quantity, 2)
            rows.append(
                {
                    "order_id": order_id,
                    "order_datetime": ordered_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "customer_name": customer["customer_name"],
                    "customer_email": customer["customer_email"],
                    "region": customer["region"],
                    "signup_date": customer["signup_date"],
                    "product_name": product["product_name"],
                    "category": product["category"],
                    "unit_price": f"{unit_price:.2f}",
                    "quantity": str(quantity),
                    "total_amount": f"{total:.2f}",
                }
            )

    # ---- inject messy records -------------------------------------------------
    n_messy = int(len(rows) * messy_fraction)
    messy_idx = rng.choice(len(rows), size=n_messy, replace=False)
    for i in messy_idx:
        kind = int(rng.integers(0, 10))
        rows[i] = _messy_variant(rows[i], kind, rng)

    # ---- inject exact duplicates (a separate class of "messy") --------------
    n_dupes = max(1, int(len(rows) * 0.01))
    dupe_idx = rng.choice(len(rows), size=n_dupes, replace=False)
    rows.extend(dict(rows[i]) for i in dupe_idx)

    # shuffle so messy/dupe rows are not clustered
    perm = rng.permutation(len(rows))
    rows = [rows[i] for i in perm]

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(RAW_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    logger.info(
        "Wrote %d raw rows (%d messy, %d duplicate) -> %s",
        len(rows), n_messy, n_dupes, out_path,
    )
    return out_path


def main() -> None:  # pragma: no cover - thin CLI wrapper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    generate()


if __name__ == "__main__":
    main()
