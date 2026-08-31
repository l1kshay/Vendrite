"""
Database load layer (Phase 1).

The ONLY module that talks to PostgreSQL. Everything here uses SQLAlchemy Core
with **reflected** table objects and **parameterized** statements -- there is no
string-concatenated SQL anywhere (the sole raw string, ``TRUNCATE ...``, is a
fixed constant with no interpolation).

Responsibilities:
  * build engines for the two DB roles (ETL = write, dashboard = read-only)
  * load validated extract rows into ``staging.raw_transactions``
  * read staging back out for transformation
  * upsert the dimensions, insert facts (idempotent via ON CONFLICT)
  * append rows to ``analytics.etl_run_log``

Convention: nothing outside the ETL/analytics jobs should ever use the ETL
engine. The dashboard uses :func:`get_engine("dashboard")`, which is granted
SELECT on the ``analytics`` schema only.
"""

from __future__ import annotations

import logging
from typing import Iterable

import numpy as np
import pandas as pd
from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from config import settings

logger = logging.getLogger(__name__)

_INSERT_CHUNK = 5000


# ---------------------------------------------------------------------------
# engine + reflection
# ---------------------------------------------------------------------------
def get_engine(role: str = "etl") -> Engine:
    """Create an :class:`Engine` for ``role`` ('etl' or 'dashboard')."""
    url = settings.etl_database_url() if role == "etl" else settings.dashboard_database_url()
    return create_engine(url, pool_pre_ping=True)


def reflect(engine: Engine) -> MetaData:
    """Reflect the staging + analytics schemas so the DDL stays the single
    source of truth for table structure."""
    md = MetaData()
    md.reflect(bind=engine, schema=settings.STAGING_SCHEMA)
    md.reflect(bind=engine, schema=settings.ANALYTICS_SCHEMA)
    return md


def _table(md: MetaData, schema: str, name: str):
    return md.tables[f"{schema}.{name}"]


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of plain-Python dicts (NaN/NaT -> None, numpy scalars
    -> native), safe to pass as bound parameters."""
    obj = df.astype(object).where(pd.notnull(df), None)
    out: list[dict] = []
    for row in obj.to_dict("records"):
        out.append(
            {k: (v.item() if isinstance(v, np.generic) else v) for k, v in row.items()}
        )
    return out


def _chunks(rows: list[dict], size: int = _INSERT_CHUNK) -> Iterable[list[dict]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


# ---------------------------------------------------------------------------
# staging
# ---------------------------------------------------------------------------
def load_to_staging(engine: Engine, valid_df: pd.DataFrame, source_file: str) -> int:
    """Replace ``staging.raw_transactions`` with ``valid_df`` (text columns +
    provenance). Returns the row count loaded."""
    md = reflect(engine)
    tbl = _table(md, settings.STAGING_SCHEMA, "raw_transactions")

    cols = [c for c in valid_df.columns if c in tbl.columns.keys()]
    frame = valid_df.loc[:, cols].copy()
    frame["source_file"] = source_file
    rows = _records(frame)

    with engine.begin() as conn:
        # plain TRUNCATE only needs the TRUNCATE privilege; RESTART IDENTITY
        # would require ownership of the identity sequence. raw_id is an
        # internal surrogate, so letting the counter keep climbing is fine.
        conn.execute(text("TRUNCATE TABLE staging.raw_transactions"))
        for batch in _chunks(rows):
            conn.execute(tbl.insert(), batch)
    logger.info("Loaded %d rows into staging.raw_transactions", len(rows))
    return len(rows)


def read_staging(engine: Engine) -> pd.DataFrame:
    """Read ``staging.raw_transactions`` back as a DataFrame for transformation."""
    md = reflect(engine)
    tbl = _table(md, settings.STAGING_SCHEMA, "raw_transactions")
    with engine.connect() as conn:
        df = pd.read_sql(select(tbl), conn)
    logger.info("Read %d rows from staging.raw_transactions", len(df))
    return df


# ---------------------------------------------------------------------------
# dimensions (idempotent upserts)
# ---------------------------------------------------------------------------
def upsert_dim_customer(engine: Engine, dim_df: pd.DataFrame) -> dict[str, int]:
    md = reflect(engine)
    tbl = _table(md, settings.ANALYTICS_SCHEMA, "dim_customer")
    rows = _records(dim_df.loc[:, ["email", "name", "signup_date", "region"]])
    if rows:
        with engine.begin() as conn:
            for batch in _chunks(rows):
                stmt = pg_insert(tbl).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["email"],
                    set_={
                        "name": stmt.excluded.name,
                        "signup_date": stmt.excluded.signup_date,
                        "region": stmt.excluded.region,
                    },
                )
                conn.execute(stmt)
    with engine.connect() as conn:
        result = conn.execute(select(tbl.c.email, tbl.c.customer_id))
        mapping = {email: cid for email, cid in result}
    logger.info("dim_customer: %d rows upserted, %d total", len(rows), len(mapping))
    return mapping


def upsert_dim_product(engine: Engine, dim_df: pd.DataFrame) -> dict[tuple[str, str], int]:
    md = reflect(engine)
    tbl = _table(md, settings.ANALYTICS_SCHEMA, "dim_product")
    rows = _records(dim_df.loc[:, ["name", "category", "unit_price"]])
    if rows:
        with engine.begin() as conn:
            for batch in _chunks(rows):
                stmt = pg_insert(tbl).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["name", "category"],
                    set_={"unit_price": stmt.excluded.unit_price},
                )
                conn.execute(stmt)
    with engine.connect() as conn:
        result = conn.execute(select(tbl.c.name, tbl.c.category, tbl.c.product_id))
        mapping = {(name, category): pid for name, category, pid in result}
    logger.info("dim_product: %d rows upserted, %d total", len(rows), len(mapping))
    return mapping


def upsert_dim_date(engine: Engine, dim_df: pd.DataFrame) -> int:
    md = reflect(engine)
    tbl = _table(md, settings.ANALYTICS_SCHEMA, "dim_date")
    rows = _records(
        dim_df.loc[:, ["date_id", "date", "day", "month", "quarter", "year", "is_weekend"]]
    )
    if rows:
        with engine.begin() as conn:
            for batch in _chunks(rows):
                stmt = pg_insert(tbl).values(batch)
                stmt = stmt.on_conflict_do_nothing(index_elements=["date_id"])
                conn.execute(stmt)
    logger.info("dim_date: %d rows upserted", len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# fact
# ---------------------------------------------------------------------------
def insert_fact_sales(
    engine: Engine,
    fact_df: pd.DataFrame,
    customer_map: dict[str, int],
    product_map: dict[tuple[str, str], int],
) -> int:
    """Resolve natural keys to surrogate ids and insert. ON CONFLICT
    (order_id, product_id) DO NOTHING keeps re-runs idempotent."""
    md = reflect(engine)
    tbl = _table(md, settings.ANALYTICS_SCHEMA, "fact_sales")

    df = fact_df.copy()
    df["customer_id"] = df["customer_email"].map(customer_map)
    df["product_id"] = df.apply(
        lambda r: product_map.get((r["product_name"], r["category"])), axis=1
    )

    unresolved = df["customer_id"].isna() | df["product_id"].isna()
    if unresolved.any():
        logger.warning(
            "Skipping %d fact rows with unresolved customer/product keys",
            int(unresolved.sum()),
        )
    df = df[~unresolved]

    payload = df.loc[
        :, ["customer_id", "product_id", "date_id", "order_id", "quantity", "total_amount"]
    ].astype({"customer_id": "int64", "product_id": "int64", "date_id": "int64"})
    rows = _records(payload)

    inserted = 0
    if rows:
        with engine.begin() as conn:
            for batch in _chunks(rows):
                stmt = pg_insert(tbl).values(batch)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["order_id", "product_id"]
                )
                result = conn.execute(stmt)
                inserted += result.rowcount or 0
    logger.info("fact_sales: %d rows submitted, ~%d newly inserted", len(rows), inserted)
    return len(rows)


# ---------------------------------------------------------------------------
# run log
# ---------------------------------------------------------------------------
def log_run(
    engine: Engine,
    status: str,
    records_processed: int | None = None,
    error_message: str | None = None,
) -> int:
    """Append a row to ``analytics.etl_run_log`` and return its ``run_id``."""
    md = reflect(engine)
    tbl = _table(md, settings.ANALYTICS_SCHEMA, "etl_run_log")
    message = str(error_message)[:2000] if error_message else None
    with engine.begin() as conn:
        result = conn.execute(
            tbl.insert().returning(tbl.c.run_id),
            {
                "status": status,
                "records_processed": records_processed,
                "error_message": message,
            },
        )
        run_id = result.scalar_one()
    logger.info("etl_run_log: run_id=%s status=%s", run_id, status)
    return run_id
