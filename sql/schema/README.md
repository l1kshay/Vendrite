# Vendrite database schema

PostgreSQL DDL for the Vendrite data warehouse. The files are numbered in the
order they must be applied.

| File | Purpose |
| ---- | ------- |
| `01_schemas.sql` | Create the `staging` and `analytics` schemas |
| `02_roles.sql` | Create the two DB roles (`vendrite_etl`, `vendrite_dashboard`) and grant privileges |
| `03_staging.sql` | `staging.raw_transactions` — raw, unvalidated landing table |
| `04_analytics.sql` | Star schema: dimensions, `fact_sales`, `customer_segments`, `sales_forecast`, `etl_run_log`, plus all constraints and indexes |

## Applying with `psql`

```bash
# as a superuser / DB owner, against an existing empty database named "vendrite"
psql -h localhost -U postgres -d vendrite -v ON_ERROR_STOP=1 -f sql/schema/01_schemas.sql
psql -h localhost -U postgres -d vendrite -v ON_ERROR_STOP=1 -f sql/schema/02_roles.sql
psql -h localhost -U postgres -d vendrite -v ON_ERROR_STOP=1 -f sql/schema/03_staging.sql
psql -h localhost -U postgres -d vendrite -v ON_ERROR_STOP=1 -f sql/schema/04_analytics.sql
```

Create the database first if needed: `createdb -h localhost -U postgres vendrite`.

## Applying with pgAdmin

pgAdmin is assumed to be used externally for DB administration — no application
code touches it. To load the schema: connect to the `vendrite` database, open
the Query Tool, and run each file above **in numeric order** (open file → F5).
The DDL is plain ANSI/PostgreSQL and is fully pgAdmin-compatible.

## Roles / least privilege

Two roles are created in `02_roles.sql`:

* **`vendrite_etl`** — `INSERT/UPDATE/DELETE/SELECT` on `staging` **and**
  `analytics`. Used only by the ETL + analytics Python jobs.
* **`vendrite_dashboard`** — `SELECT` on `analytics` **only**. It has *no*
  grant on `staging`, so the dashboard physically cannot read
  `staging.raw_transactions`. The dashboard and analytics query code is also
  restricted to the `analytics` schema by convention (see module docstrings).

> Set real passwords: `02_roles.sql` creates the roles with placeholder
> passwords. Change them immediately, e.g.
> `ALTER ROLE vendrite_dashboard WITH PASSWORD '...';`, and put the same values
> in your `.env` / CI secrets.

## Star schema at a glance

```
              dim_customer            dim_product            dim_date
              (customer_id PK)        (product_id PK)        (date_id PK)
                    ^                       ^                     ^
                    |                       |                     |
                    +-----------+-----------+----------+----------+
                                |                      |
                            fact_sales (sale_id PK, FKs NOT NULL)
                                order_id, quantity, total_amount

  customer_segments   -> FK customer_id; one row appended per computation cycle
  sales_forecast      -> standalone; versioned by model_version (no FK to facts)
  etl_run_log         -> one row per pipeline run
```
