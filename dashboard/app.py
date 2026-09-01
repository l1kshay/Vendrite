"""
Vendrite dashboard — Streamlit + Plotly, multi-page.

Presentation only. Reads pre-computed rows from the ``analytics`` schema
through the **read-only** ``vendrite_dashboard`` role and renders them; no
ingestion / transformation / RFM / CLV / forecasting logic lives here.

Layout
------
This file is the entry script: it configures the page, runs the login gate
once, then hands off to ``st.navigation``. Each page is a ``render()`` function
in ``dashboard/views/`` -- data access is centralised in ``dashboard/data.py``,
pure reshaping in ``dashboard/transforms.py``, and colours / formatting in
``dashboard/theme.py``.

Run:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import streamlit as st

# --- ensure the project root is importable, however this file is launched ----
# `python -m ...` and pytest put the repo root on sys.path; Streamlit Community
# Cloud's launcher does not. Add it before the first-party imports below.
import sys as _sys
from pathlib import Path as _Path

_ROOT = str(_Path(__file__).resolve().parents[1])
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Vendrite", page_icon="📊", layout="wide")

from dashboard.auth import require_login
from dashboard.views import forecasting_view, overview, retention, segments


# Streamlit executes this file top-to-bottom on every rerun, so the app body
# runs at module scope (no __main__ guard -- that is the Streamlit convention).
require_login()
st.sidebar.title("📊 Vendrite")
st.sidebar.caption("Sales & customer analytics")

# Every page callable is named ``render``; give each an explicit url_path so
# Streamlit does not collide them.
_nav = st.navigation(
    {
        "Analytics": [
            st.Page(overview.render, title="Overview", icon="📈", url_path="overview", default=True),
            st.Page(segments.render, title="Segments & CLV", icon="🧭", url_path="segments"),
            st.Page(retention.render, title="Retention", icon="🔁", url_path="retention"),
        ],
        "Models": [
            st.Page(forecasting_view.render, title="Forecasting", icon="🔮", url_path="forecasting"),
        ],
    }
)
_nav.run()
