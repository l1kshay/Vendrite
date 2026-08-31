# Vendrite

E-commerce sales & customer analytics platform — an ETL pipeline, RFM customer
segmentation, and short-term demand forecasting, surfaced through an interactive
Streamlit + Plotly dashboard, backed by a PostgreSQL star-schema warehouse.

> **Build status:** Phases 1–2 complete (Data Foundation + Analytics Layer:
> ETL → star schema, RFM segmentation, linear-regression forecast, all
> DB-verified). Phases 3–5 (dashboard, automation, security/testing/deploy)
> are scaffolded and in progress.

---

## Architecture overview

```
                 ┌─────────────────────────┐
 raw CSV  ──────▶│ etl/extract.py          │  read + schema/format validation
 (mock or real)  │  → valid rows            │  malformed rows → data/processed/
                 │  → quarantine (reason)   │      quarantine_extract.csv
                 └───────────┬─────────────┘
                             ▼
                 ┌─────────────────────────┐
                 │ etl/load.py             │  parameterized SQLAlchemy Core
                 │  load_to_staging()      │  ── writes ▶ staging.raw_transactions
                 └───────────┬─────────────┘
                             ▼  read_staging()
                 ┌─────────────────────────┐
                 │ etl/clean.py (pure)     │  dedupe · impute · coerce types ·
                 │  transform()            │  map → star schema
                 │  → dim_* / fact_sales   │  invalid rows → quarantine_transform.csv
                 └───────────┬─────────────┘
                             ▼  upsert / insert (ON CONFLICT = idempotent)
                 ┌───────────────────────────────────────────────────────────┐
                 │ analytics schema (PostgreSQL star schema)                  │
                 │   dim_customer   dim_product   dim_date                    │
                 │              ╲       │       ╱                             │
                 │                 fact_sales                                 │
                 │   customer_segments (Phase 2)   sales_forecast (Phase 2)   │
                 │   etl_run_log  ◀── every pipeline run logs STARTED/SUCCESS/FAILED
                 └───────────┬───────────────────────────────┬───────────────┘
                             ▼ (read-only role)               ▼ (read-only role)
                 ┌─────────────────────────┐     ┌───────────────────────────┐
                 │ analytics/segmentation  │     │ dashboard/app.py          │
                 │ analytics/forecasting   │     │  Streamlit + Plotly       │
                 │  (Phase 2)              │     │  auth gate (Phase 5)      │
                 └─────────────────────────┘     └───────────────────────────┘
```

**Separation of concerns** — ingestion (`extract`), transformation (`clean`,
pure/no-I/O), persistence (`load`, the only DB module), analytics (`analytics/`),
and presentation (`dashboard/`) never share a file. All configuration
(paths, DB params, tunables) lives in `config/settings.py`, loaded from
environment variables.

### Project layout

| Path | Purpose |
| ---- | ------- |
| `data/raw/`, `data/processed/` | generated raw CSV / intermediate + quarantine files (gitignored) |
| `etl/` | `generate_mock_data`, `extract`, `clean`, `load`, `run_etl` |
| `sql/schema/` | numbered DDL + role setup (see `sql/schema/README.md`) |
| `sql/queries/` | verification / analysis SQL |
| `analytics/` | `segmentation.py`, `forecasting.py` (Phase 2) |
| `dashboard/` | `app.py` — Streamlit dashboard (Phase 3) |
| `reports/` | generated summary report artifacts (Phase 4) |
| `config/settings.py` | central configuration, env-driven |
| `tests/` | pytest suite (Phase 5) |
| `.github/workflows/` | scheduled pipeline (Phase 4) |

---

## Setup

### 1. Python environment

Requires **Python 3.11+** (built/tested on CPython 3.14).

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configuration

```bash
cp .env.example .env
```

Edit `.env` and set real values (the file is gitignored — never commit it).
In CI these same keys come from **GitHub Actions Secrets**.

### 3. PostgreSQL

Assumes a reachable PostgreSQL server and a database named `vendrite`
(pgAdmin is used externally for admin — no app code touches it).

```bash
createdb -h localhost -U postgres vendrite

psql -h localhost -U postgres -d vendrite -v ON_ERROR_STOP=1 -f sql/schema/01_schemas.sql
psql -h localhost -U postgres -d vendrite -v ON_ERROR_STOP=1 -f sql/schema/02_roles.sql
psql -h localhost -U postgres -d vendrite -v ON_ERROR_STOP=1 -f sql/schema/03_staging.sql
psql -h localhost -U postgres -d vendrite -v ON_ERROR_STOP=1 -f sql/schema/04_analytics.sql
```

`02_roles.sql` creates the two roles with **placeholder passwords** — change
them immediately and put the same values in `.env`:

```sql
ALTER ROLE vendrite_etl       WITH PASSWORD '...';
ALTER ROLE vendrite_dashboard WITH PASSWORD '...';
```

| Role | Access | Used by |
| ---- | ------ | ------- |
| `vendrite_etl` | read/write on `staging` + `analytics` | ETL & analytics jobs |
| `vendrite_dashboard` | **read-only on `analytics` only** | Streamlit dashboard |

Full details and the pgAdmin path: [`sql/schema/README.md`](sql/schema/README.md).

---

## Running the pipeline locally

```bash
# Full pipeline against PostgreSQL: (re)generate mock data → extract → stage →
# transform → load star schema → log to analytics.etl_run_log
python -m etl.run_etl --generate

# Same, but reuse the existing raw CSV
python -m etl.run_etl

# DB-free dev/inspection: generate → extract → clean, writing the star-schema
# frames as CSVs to data/processed/ (no PostgreSQL needed)
python -m etl.run_etl --offline --generate
```

Individual stages are runnable too: `python -m etl.generate_mock_data`,
`python -m etl.extract`.

### Verifying Phase 1

After a full run, check the warehouse directly (psql or pgAdmin):

```bash
psql -h localhost -U vendrite_etl -d vendrite -f sql/queries/phase1_verification.sql
```

Expect: `staging.raw_transactions` populated; `dim_customer` / `dim_product` /
`dim_date` / `fact_sales` non-empty; zero orphan FKs; zero rows with
`quantity <= 0` or `total_amount <= 0`; a `SUCCESS` row in `etl_run_log`.

---

## Analytics layer

Run after the ETL has populated `analytics.fact_sales`:

```bash
# RFM segmentation → appends one row per customer to analytics.customer_segments
python -m analytics.segmentation

# Daily-revenue linear-regression forecast → appends N days to
# analytics.sales_forecast, tagged with VENDRITE_FORECAST_MODEL_VERSION
python -m analytics.forecasting
```

**Segmentation** — recency (days since last order), frequency (distinct orders),
monetary (total spend) per customer, each scored into 1–5 quintiles, then a
first-match rule table assigns one of: `Champion`, `Loyal`, `New`, `At Risk`,
`Hibernating`, `Needs Attention`. `customer_segments` is **append-only** — each
run adds a new `computed_date` cohort so segment history is preserved.

**Forecasting** — `fact_sales` aggregated to a gap-free daily revenue series;
an explainable OLS `LinearRegression` on `revenue ~ b0 + b_t·t + Σ b_dow·[weekday]`
(so `b_t` is the revenue trend per day and the weekday one-hots capture weekly
shape). Predictions are clipped at 0 and written to `sales_forecast` with
`model_version` — the table is standalone (no FK to `fact_sales`) so forecast
versions stay independent.

---

## Scheduled job (Phase 4 — in progress)

A GitHub Actions workflow (`.github/workflows/`) will run the full chain on a
cron schedule: **ETL → analytics (segmentation + forecasting) → summary report**.
It sources DB credentials from GitHub Actions Secrets, writes a `STARTED` then
`SUCCESS`/`FAILED` row to `analytics.etl_run_log` for every run, fails the
workflow loudly on any error, and uploads the generated `reports/` artifact.

---

## Power BI companion report (Phase 3 — optional, documented only)

Power BI Desktop is a manual, out-of-scope companion. To point it at the
warehouse: **Get Data → PostgreSQL database**, server `="<host>:5432"`, database
`vendrite`, connect with the `vendrite_dashboard` (read-only) role, and import
the `analytics` schema tables. Model `fact_sales` against the three dimensions on
their `*_id` keys.

---

## Deployment (Phase 5 — in progress)

The dashboard will be deployed to Streamlit Community Cloud (or Render). Both
platforms terminate TLS and serve the app over **HTTPS by default**; secrets are
supplied through the platform's secrets manager, mirroring `.env`. A
`streamlit-authenticator` credential login gate is placed in front of the
dashboard before any deployment step.

---

## Security notes

- No hardcoded credentials — `.env` locally (gitignored), Actions Secrets in CI.
- Database access is exclusively via SQLAlchemy Core with **parameterized**
  statements / reflected tables — no string-concatenated SQL.
- Two least-privilege DB roles; the dashboard role cannot read `staging`.
- Ingestion validates every row; malformed records are **quarantined with a
  reason**, never silently dropped.
- Analytics/dashboard code queries the `analytics` schema only.
