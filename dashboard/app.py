"""
Vendrite dashboard — Streamlit + Plotly, multi-page.

Presentation only. Reads pre-computed rows from the ``analytics`` schema
through the **read-only** ``vendrite_dashboard`` role and renders them; no
ingestion / transformation / RFM / CLV / forecasting logic lives here.

Layout
------
This file is the entry script / router. Every run, in order:

1. ``authenticate()`` — mount the cookie, decide if the user is signed in.
2. If not: render the login card, then a **hidden** one-page ``st.navigation``
   (this is what clears a sidebar nav left over on the browser from an earlier
   authenticated run — see ``dashboard/auth`` for why), then stop.
3. If signed in: logout control, Presentation-mode toggle, the real
   ``st.navigation`` page tree, then ``.run()`` the selected page.

Sidebar order (top → bottom): wordmark (CSS ``::before`` on the nav) · nav
groups · Filters expander · Presentation-mode toggle · Log out. The last three
are ordered by CSS (``.st-key-vd-*`` + flex ``order``) in ``theme.py`` because
Streamlit always paints the nav first and user content in call order.

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

from dashboard.theme import inject_css, inject_login_css, inject_mode_css

inject_css()

from dashboard.auth import authenticate, render_login_form, render_logout
from dashboard.views import forecasting_view, overview, retention, segments


def _noop() -> None:  # placeholder page target for the logged-out state
    return None


# Streamlit executes this file top-to-bottom on every rerun.
#
# NEVER call st.rerun() between a streamlit-authenticator call and the end of
# the run. It writes AND deletes its re-auth cookie through
# extra_streamlit_components' CookieManager, which is a Streamlit *component*:
# a rerun discards the run's pending deltas, so the component never reaches the
# browser and the cookie is silently never written (login doesn't survive a
# refresh) or never deleted (logout doesn't survive a refresh). Every state
# transition below therefore finishes the run on the correct branch instead.
_authed = authenticate()

if _authed:
    render_logout()  # sidebar Log out; clears the session + queues the delete
    _authed = st.session_state.get("authentication_status") is True

if not _authed:
    inject_login_css()  # no sidebar region on the login screen
    render_login_form()
    _authed = st.session_state.get("authentication_status") is True

if not _authed:
    # A hidden nav still transmits a navigation message, which REPLACES any
    # sidebar nav the browser is holding from a previous authenticated run.
    # Without this call the logged-in nav stays on screen next to the login
    # card until a manual refresh.
    st.navigation(
        [st.Page(_noop, title="Sign in", url_path="signin")],
        position="hidden",
    ).run()
    st.stop()

# ---------------------------------------------------------------------------
# authenticated
# ---------------------------------------------------------------------------

with st.sidebar.container(key="vd-presentation"):
    st.toggle(
        "Presentation mode",
        value=True,
        key="presentation_mode",
        help="On: a clean, demo-ready view — chart toolbars, the app menu and "
             "(on Streamlit Cloud) the Manage-app pill are hidden, and the "
             "Filters panel starts collapsed. Off: everything comes back for "
             "exploring the data.",
    )
inject_mode_css(st.session_state["presentation_mode"])

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
