"""
Vendrite Streamlit dashboard -- Phase 3 (not yet implemented).

Planned:
  * streamlit-authenticator login gate (credentials from env vars) -- Phase 5
  * KPI cards, sales-trend charts, filterable segment views, date/category
    drill-down -- all Plotly inside Streamlit
  * queries run through the READ-ONLY dashboard DB role and touch ONLY the
    analytics schema

Run (once implemented):  streamlit run dashboard/app.py
"""

from __future__ import annotations

import streamlit as st


def main() -> None:  # pragma: no cover
    st.title("Vendrite")
    st.info("Dashboard is implemented in Phase 3.")


if __name__ == "__main__":
    main()
