"""
Central configuration for Vendrite.

Single source of truth for:
  * filesystem paths
  * database connection parameters (two roles: ETL + dashboard)
  * mock-data-generator knobs
  * forecasting knobs
  * dashboard auth knobs

Everything is loaded from environment variables (via a local, gitignored
``.env`` file in development, or GitHub Actions Secrets in CI). No path,
credential, or tunable is hardcoded in the ETL / analytics / dashboard code
itself -- import it from here.

Design notes
------------
* Reading a *missing* env var does NOT raise at import time. Scripts that do
  not touch the database (e.g. the mock-data generator) must still be able to
  ``import config.settings``. Database helpers call :func:`require_db_env`
  explicitly and raise a clear error if something is missing.
* Database URLs are built with :class:`sqlalchemy.engine.URL` -- never by
  string concatenation -- so passwords with special characters are escaped
  correctly and there is no SQL/DSN-injection surface.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL

# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------
# Project root = the directory that contains this ``config/`` package.
BASE_DIR: Path = Path(__file__).resolve().parents[1]

# Load <root>/.env if present. ``override=False`` => real environment wins,
# which is what we want in CI.
load_dotenv(BASE_DIR / ".env", override=False)


# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------
DATA_DIR: Path = BASE_DIR / "data"
DATA_RAW_DIR: Path = DATA_DIR / "raw"
DATA_PROCESSED_DIR: Path = DATA_DIR / "processed"
SQL_DIR: Path = BASE_DIR / "sql"
REPORTS_DIR: Path = BASE_DIR / "reports"

# Canonical artifact filenames produced by the pipeline.
RAW_TRANSACTIONS_CSV: Path = DATA_RAW_DIR / "transactions_raw.csv"
QUARANTINE_EXTRACT_CSV: Path = DATA_PROCESSED_DIR / "quarantine_extract.csv"
QUARANTINE_TRANSFORM_CSV: Path = DATA_PROCESSED_DIR / "quarantine_transform.csv"


def ensure_dirs() -> None:
    """Create the data/report output directories if they do not exist."""
    for path in (DATA_RAW_DIR, DATA_PROCESSED_DIR, REPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_DRIVER = "postgresql+psycopg2"

DB_HOST: str | None = os.getenv("VENDRITE_DB_HOST", "localhost")
DB_PORT: int = int(os.getenv("VENDRITE_DB_PORT", "5432"))
DB_NAME: str | None = os.getenv("VENDRITE_DB_NAME", "vendrite")

# ETL role -- write access to staging + analytics.
ETL_DB_USER: str | None = os.getenv("VENDRITE_ETL_DB_USER")
ETL_DB_PASSWORD: str | None = os.getenv("VENDRITE_ETL_DB_PASSWORD")

# Dashboard role -- read-only access to the analytics schema only.
DASHBOARD_DB_USER: str | None = os.getenv("VENDRITE_DASHBOARD_DB_USER")
DASHBOARD_DB_PASSWORD: str | None = os.getenv("VENDRITE_DASHBOARD_DB_PASSWORD")

STAGING_SCHEMA = "staging"
ANALYTICS_SCHEMA = "analytics"


class ConfigError(RuntimeError):
    """Raised when required configuration is absent."""


def require_db_env(role: str) -> None:
    """Validate that the env vars for ``role`` ('etl' or 'dashboard') are set.

    Raises :class:`ConfigError` listing every missing variable.
    """
    common = {
        "VENDRITE_DB_HOST": DB_HOST,
        "VENDRITE_DB_NAME": DB_NAME,
    }
    if role == "etl":
        specific = {
            "VENDRITE_ETL_DB_USER": ETL_DB_USER,
            "VENDRITE_ETL_DB_PASSWORD": ETL_DB_PASSWORD,
        }
    elif role == "dashboard":
        specific = {
            "VENDRITE_DASHBOARD_DB_USER": DASHBOARD_DB_USER,
            "VENDRITE_DASHBOARD_DB_PASSWORD": DASHBOARD_DB_PASSWORD,
        }
    else:  # pragma: no cover - programmer error
        raise ValueError(f"unknown DB role: {role!r}")

    missing = [name for name, value in {**common, **specific}.items() if not value]
    if missing:
        raise ConfigError(
            "Missing required environment variables for the "
            f"'{role}' database role: {', '.join(missing)}. "
            "Copy .env.example to .env and fill it in."
        )


def _database_url(user: str | None, password: str | None) -> URL:
    return URL.create(
        DB_DRIVER,
        username=user,
        password=password,
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
    )


def etl_database_url() -> URL:
    """SQLAlchemy URL for the write-enabled ETL role."""
    require_db_env("etl")
    return _database_url(ETL_DB_USER, ETL_DB_PASSWORD)


def dashboard_database_url() -> URL:
    """SQLAlchemy URL for the read-only dashboard role."""
    require_db_env("dashboard")
    return _database_url(DASHBOARD_DB_USER, DASHBOARD_DB_PASSWORD)


# ---------------------------------------------------------------------------
# Mock data generator (Phase 1)
# ---------------------------------------------------------------------------
MOCK_SEED: int = int(os.getenv("VENDRITE_MOCK_SEED", "42"))
MOCK_N_CUSTOMERS: int = int(os.getenv("VENDRITE_MOCK_N_CUSTOMERS", "250"))
MOCK_N_PRODUCTS: int = int(os.getenv("VENDRITE_MOCK_N_PRODUCTS", "50"))
MOCK_N_ORDERS: int = int(os.getenv("VENDRITE_MOCK_N_ORDERS", "9000"))
MOCK_MONTHS_BACK: int = int(os.getenv("VENDRITE_MOCK_MONTHS_BACK", "12"))
# Fraction of generated rows deliberately corrupted so cleaning has real work.
MOCK_MESSY_FRACTION: float = float(os.getenv("VENDRITE_MOCK_MESSY_FRACTION", "0.08"))

MOCK_REGIONS: tuple[str, ...] = ("North", "South", "East", "West", "Central")
MOCK_CATEGORIES: tuple[str, ...] = (
    "Electronics",
    "Home & Kitchen",
    "Books",
    "Clothing",
    "Sports",
    "Toys",
    "Beauty",
)


# ---------------------------------------------------------------------------
# Forecasting (Phase 2)
# ---------------------------------------------------------------------------
FORECAST_MODEL_VERSION: str = os.getenv("VENDRITE_FORECAST_MODEL_VERSION", "linreg-v1")
FORECAST_HORIZON_DAYS: int = int(os.getenv("VENDRITE_FORECAST_HORIZON_DAYS", "30"))


# ---------------------------------------------------------------------------
# Dashboard auth (Phase 5)
# ---------------------------------------------------------------------------
AUTH_COOKIE_NAME: str = os.getenv("VENDRITE_AUTH_COOKIE_NAME", "vendrite_auth")
AUTH_COOKIE_KEY: str | None = os.getenv("VENDRITE_AUTH_COOKIE_KEY")
AUTH_COOKIE_EXPIRY_DAYS: int = int(os.getenv("VENDRITE_AUTH_COOKIE_EXPIRY_DAYS", "7"))
AUTH_USERNAME: str | None = os.getenv("VENDRITE_AUTH_USERNAME")
AUTH_NAME: str | None = os.getenv("VENDRITE_AUTH_NAME")
AUTH_EMAIL: str | None = os.getenv("VENDRITE_AUTH_EMAIL")
AUTH_PASSWORD_HASH: str | None = os.getenv("VENDRITE_AUTH_PASSWORD_HASH")
