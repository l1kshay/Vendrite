# Vendrite

E-commerce sales & customer analytics platform — an ETL pipeline, RFM customer
segmentation, and short-term demand forecasting, surfaced through an interactive
Streamlit + Plotly dashboard, backed by a PostgreSQL star-schema warehouse.

> **Build status:** All 5 phases complete — ETL → PostgreSQL star schema,
> RFM segmentation, linear-regression forecast, Streamlit/Plotly dashboard
> behind a credential login gate, scheduled GitHub Actions pipeline + templated
> run report, and a pytest suite. All DB-verified end to end.

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
| `reporting/` | `generate_report.py` + Jinja2 `templates/` — run-summary report (Phase 4) |
| `reports/` | generated summary report artifacts, gitignored (Phase 4) |
| `run_pipeline.py` | one-command orchestrator: ETL → analytics → report (Phase 4) |
| `config/settings.py` | central configuration, env-driven |
| `tests/` | pytest suite — `test_clean.py`, `test_segmentation.py`, `test_forecasting.py` |
| `.github/workflows/pipeline.yml` | scheduled ETL→analytics→report pipeline |
| `.github/workflows/tests.yml` | run `pytest` on every push / PR |

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

## Dashboard

```bash
streamlit run dashboard/app.py
```

`dashboard/app.py` connects with the **read-only `vendrite_dashboard` role** and
reads only the `analytics` schema (all SQL is parameterised `sqlalchemy.text`;
sidebar filtering is pure in-memory pandas). It performs no ETL/analytics logic —
just renders the materialised results. Contents:

- **Sidebar filters** — date range, category, region. All charts and KPIs react.
- **KPI cards** — revenue, orders, units, average order value, active customers,
  each with a delta vs the equal-length preceding period.
- **Sales trend** — Plotly area chart with a Daily / Weekly / Monthly toggle and
  a 7-day rolling average.
- **Forecast** — last 90 days actual vs the `sales_forecast` horizon (dashed),
  split by a marker at the last actual day.
- **Revenue by category / region** — horizontal bar charts.
- **Customer segments (RFM)** — segment distribution bar, an avg-R/F/M profile
  table, and a **drill-down**: pick a segment → its customer list (with CSV
  download).
- **Category drill-down** — pick a category → monthly revenue + top-10 products.
- **Pipeline status** — the recent `etl_run_log` rows.

### Login gate

`main()` is guarded by a `streamlit-authenticator` credential login. Credentials
come **only** from `VENDRITE_AUTH_*` environment variables (see `.env.example`)
and the password is stored as a **bcrypt hash**, never plaintext. To provision:

```bash
# 1. a random cookie-signing key
python -c "import secrets; print(secrets.token_hex(32))"        # -> VENDRITE_AUTH_COOKIE_KEY

# 2. a bcrypt hash of the login password
python -c "import bcrypt,getpass; print(bcrypt.hashpw(getpass.getpass('pw: ').encode(), bcrypt.gensalt()).decode())"
#   -> VENDRITE_AUTH_PASSWORD_HASH   (also set VENDRITE_AUTH_USERNAME / _NAME / _EMAIL)
```

If the auth vars are missing the dashboard shows a configuration error and
refuses to load any data.

---

## Automation & reporting

### Full pipeline in one command

```bash
python run_pipeline.py --generate      # ETL → segmentation → forecast → report
python run_pipeline.py --skip-report   # stop after analytics
```

`run_pipeline.py` runs the four stages in order. Every stage writes
`STARTED` then `SUCCESS`/`FAILED` rows to `analytics.etl_run_log`; any stage
failure propagates, the process exits non-zero, and the FAILED row is already
in the DB. The report stage is read-only.

### Run-summary report

`reporting/generate_report.py` reads the `analytics` schema and renders two
Jinja2 templates (`reporting/templates/summary.{md,html}.j2`) into `reports/`:

- `summary_<UTC-timestamp>.md` and `.html` — the immutable run artifact
- `latest.md` / `latest.html` — always the most recent

Contents: warehouse row counts, all-time + last-30d-vs-prior-30d sales KPIs,
top categories/products, the latest customer-segment cohort with average RFM,
the current forecast, extract/transform quarantine counts, and the recent
`etl_run_log` tail. `reports/` output is gitignored (regenerated every run).

### Scheduled GitHub Actions workflow

`.github/workflows/pipeline.yml` runs daily at **02:00 UTC** (and on demand via
*Run workflow*):

1. spins up an ephemeral `postgres:16` service,
2. installs pinned deps, applies `sql/schema/01–04`, sets the two role
   passwords,
3. runs `python run_pipeline.py --generate`,
4. **uploads `reports/` as a build artifact** (`vendrite-report-<run#>`,
   30-day retention) — on success *and* failure,
5. on failure, dumps the `etl_run_log` tail into the job log.

Any error makes the job go red (`ON_ERROR_STOP=1` for schema steps; non-zero
exit from `run_pipeline.py`).

**Repository secrets** (Settings → Secrets and variables → Actions). All are
optional for the CI/ephemeral-DB path (safe fallbacks are used) and
**required** when pointing at a real database:

| Secret | Purpose |
| --- | --- |
| `VENDRITE_SUPERUSER_PASSWORD` | Postgres superuser, for schema + role provisioning |
| `VENDRITE_ETL_DB_PASSWORD` | password set for / used by the `vendrite_etl` role |
| `VENDRITE_DASHBOARD_DB_PASSWORD` | password set for / used by the `vendrite_dashboard` role |

To target a managed database instead of the CI service, delete the `services:`
block in the workflow and add `VENDRITE_DB_HOST` / `VENDRITE_DB_PORT` /
`VENDRITE_DB_NAME` (as secrets or `env:`), then drop `--generate` if you have a
real upstream source.

---

## Power BI companion report (optional — documented only, not built)

Power BI Desktop is a manual companion outside the automated pipeline. No `.pbix`
file is produced by this repo; connect it to the same warehouse:

1. **Get Data → PostgreSQL database.**
   - Server: `localhost:5432` (or your host)
   - Database: `vendrite`
   - Data Connectivity mode: **Import** (or DirectQuery for live refresh)
2. **Credentials:** use the read-only **`vendrite_dashboard`** role — never the
   ETL role. Power BI only needs `SELECT` on `analytics`.
3. **Select tables** from the `analytics` schema: `fact_sales`, `dim_customer`,
   `dim_product`, `dim_date`, `customer_segments`, `sales_forecast`. Do **not**
   import the `staging` schema (the dashboard role cannot see it anyway).
4. **Model** (Model view): relationships
   - `fact_sales[customer_id]` → `dim_customer[customer_id]`
   - `fact_sales[product_id]`  → `dim_product[product_id]`
   - `fact_sales[date_id]`     → `dim_date[date_id]`
   - `customer_segments[customer_id]` → `dim_customer[customer_id]`
   Mark `dim_date` as the date table (on `dim_date[date]`).
5. **Suggested measures:**
   `Revenue = SUM(fact_sales[total_amount])`,
   `Orders = DISTINCTCOUNT(fact_sales[order_id])`,
   `Units = SUM(fact_sales[quantity])`,
   `AOV = DIVIDE([Revenue], [Orders])`.
6. **Refresh:** schedule it to run after the GitHub Actions pipeline (Phase 4)
   so the report always trails a completed ETL run.

---

## Testing

```bash
pytest
```

Pure unit tests (no database) covering the two modules most likely to hide
silent bugs, plus the forecasting math:

| File | Covers |
| --- | --- |
| `tests/test_clean.py` | whitespace/case standardisation, exact + order-line dedupe, type coercion, region imputation, **total reconciliation** (blank / absurd / consistent / invalid-input cases), quarantine reasons, all star-schema builders, and one end-to-end `transform()` on **deliberately messy input** (dupes, casing, blank & absurd totals, zero qty, negative price, bad date, missing product) |
| `tests/test_segmentation.py` | `compute_rfm` values, **multi-line orders counting as one order**, analysis-window filtering, quintile direction/bounds + small-sample fallback, the full segment rule table, and end-to-end extremes (clear Champion / Hibernating) |
| `tests/test_forecasting.py` | gap-filling to a daily series, feature matrix shape, linear-trend recovery, negative-prediction clipping |

`.github/workflows/tests.yml` runs `pytest` on every push and PR.

---

## Deployment

The dashboard deploys to **Streamlit Community Cloud** (or **Render**); both
terminate TLS and serve over **HTTPS by default**. The login gate must be
configured before deploying.

### Streamlit Community Cloud

1. Push to GitHub, then *New app* → pick the repo/branch, main file
   `dashboard/app.py`.
2. **Advanced settings → Secrets** — add, in TOML form, the same keys as `.env`:
   `VENDRITE_DB_HOST/PORT/NAME`, `VENDRITE_DASHBOARD_DB_USER/PASSWORD` (the app
   only needs the **read-only** role), and `VENDRITE_AUTH_*`. `config/settings.py`
   reads `os.environ`, and Streamlit Cloud injects `st.secrets` into the
   environment, so no code change is needed.
3. The database must be reachable from Streamlit Cloud — use a managed Postgres
   (Neon, Supabase, RDS, …) and run `sql/schema/01–04` against it once.

### Render

1. *New → Web Service* from the repo.
2. Build: `pip install -r requirements.txt`;
   Start: `streamlit run dashboard/app.py --server.port $PORT --server.address 0.0.0.0`.
3. Add the same environment variables under *Environment*. Point at a Render
   PostgreSQL instance (or external managed DB).

The **ETL/analytics pipeline is not deployed with the dashboard** — it runs on
the GitHub Actions schedule (or any cron host) with the write-enabled
`vendrite_etl` role and populates the shared warehouse the dashboard reads.

---

## Security notes

- **No hardcoded credentials.** Everything sensitive comes from env vars —
  `.env` locally (gitignored), GitHub Actions Secrets in CI, the platform
  secrets manager in deployment. `.env.example` documents every key.
- **No string-concatenated SQL.** All DB access is SQLAlchemy Core with
  reflected tables and bound parameters / `sqlalchemy.text(:param)`; dashboard
  filtering never touches SQL (pure pandas).
- **Two least-privilege DB roles.** `vendrite_etl` (read/write) is used only by
  the pipeline; `vendrite_dashboard` (SELECT on `analytics` only) is used by the
  dashboard and Power BI and **cannot read `staging`** or write anything —
  enforced by GRANTs and verified in testing.
- **Validated ingestion.** Every row is schema- and format-checked; malformed
  records are quarantined to `data/processed/quarantine_*.csv` with a reason,
  never silently dropped. Inconsistent totals are reconciled, not trusted.
- **Schema isolation.** Analytics and dashboard code query the `analytics`
  schema only, by convention *and* by the dashboard role's lack of `staging`
  privileges.
- **Auth before deploy.** The dashboard is behind a `streamlit-authenticator`
  login; the password lives only as a bcrypt hash. Deployment platforms add
  HTTPS on top.

---

## Design notes

- **Separation of concerns** — ingestion (`etl/extract`), transformation
  (`etl/clean`, pure/no-I/O), persistence (`etl/load`, the only DB-writing
  module), analytics (`analytics/`), presentation (`dashboard/`), and reporting
  (`reporting/`) never share a file. `run_pipeline.py` only *wires* them.
- **The DDL is the single source of truth** — `etl/load.py` reflects tables at
  runtime instead of redeclaring their structure.
- **Idempotent loads** — dimensions upsert `ON CONFLICT`, facts insert
  `ON CONFLICT (order_id, product_id) DO NOTHING`, so re-running the pipeline
  converges rather than duplicating. Re-processing *corrected* source data needs
  a truncate + reload (or switching the fact loader to `DO UPDATE`).
- **Append-only history** — `customer_segments` gets a fresh cohort per run;
  `sales_forecast` is versioned by `model_version` and never joined to facts, so
  forecast iterations stay independent. Consumers use `DISTINCT ON` / latest
  `generated_date` to get the current view.
- **Explainable forecasting** — plain OLS on a day index + weekday one-hots; the
  `t` coefficient *is* the revenue trend per day. No black box.
- **Every run is logged** — each stage writes `STARTED`/`SUCCESS`/`FAILED` to
  `analytics.etl_run_log`; failures are loud (non-zero exit, red CI).
- **Python 3.14 note** — dependency pins were resolved to 3.14-compatible wheels
  (pandas 3.0, numpy 2.5, …); the same tool set installs on 3.11–3.13.
