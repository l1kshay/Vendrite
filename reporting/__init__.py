"""Vendrite automated reporting.

Renders a templated (Markdown + HTML via Jinja2) run summary from the
``analytics`` schema after a successful pipeline run and saves it to
``reports/``. Presentation of already-computed results only -- no ETL,
transformation, or analytics logic here.
"""
