"""Vendrite ETL package.

Strict separation of concerns -- each module has ONE job:

* ``generate_mock_data`` -- synthesize a realistic (deliberately messy) raw CSV.
* ``extract``            -- read the raw CSV, validate schema/format, quarantine
                            malformed rows, hand back structurally-valid rows.
* ``clean``              -- pure transformation logic: dedupe, impute, standardize
                            types, map into star-schema-shaped frames. No I/O.
* ``load``               -- all database I/O (SQLAlchemy Core, parameterized).
* ``run_etl``            -- orchestrator / CLI that wires the stages together and
                            writes to analytics.etl_run_log.
"""
