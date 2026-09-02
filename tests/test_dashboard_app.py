"""
Headless render tests for the dashboard entry script (``dashboard/app.py``),
driven by ``streamlit.testing.v1.AppTest``. No browser, no DB.

These cover the login gate's boundaries: the unauthenticated screen must show
*only* the login card (no nav, no sidebar chrome), and the screen produced by
clicking "Logout" must fall back to that same bare login state rather than
rendering the full app beside the form (regression test for the missing rerun
in ``require_login``).
"""

from __future__ import annotations

from pathlib import Path

import bcrypt
import pytest
from streamlit.testing.v1 import AppTest

import dashboard.auth as auth
from config import settings

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
_HASH = bcrypt.hashpw(b"test-pw", bcrypt.gensalt()).decode()


@pytest.fixture
def configured_auth(monkeypatch):
    """Make the auth gate believe it is configured, without a real ``.env``."""
    monkeypatch.setattr(settings, "AUTH_USERNAME", "analyst", raising=False)
    monkeypatch.setattr(settings, "AUTH_NAME", "Analyst", raising=False)
    monkeypatch.setattr(settings, "AUTH_EMAIL", "analyst@example.com", raising=False)
    monkeypatch.setattr(settings, "AUTH_PASSWORD_HASH", _HASH, raising=False)
    monkeypatch.setattr(settings, "AUTH_COOKIE_NAME", "vendrite_auth", raising=False)
    monkeypatch.setattr(settings, "AUTH_COOKIE_KEY", "test-cookie-key", raising=False)
    monkeypatch.setattr(settings, "AUTH_COOKIE_EXPIRY_DAYS", 7, raising=False)
    # _AUTH_ENV is frozen at import time from the above; rebuild it non-empty
    monkeypatch.setattr(
        auth,
        "_AUTH_ENV",
        {k: "set" for k in auth._AUTH_ENV},
        raising=False,
    )


def test_unauthenticated_render_is_only_the_login_card(configured_auth):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()

    assert not at.exception
    # the login form, and nothing that belongs to the authenticated app
    assert [ti.label for ti in at.text_input] == ["Username", "Password"]
    assert at.sidebar.button == []
    assert at.sidebar.toggle == []          # no "Presentation mode"
    assert at.title == []                   # no page rendered -> st.navigation never ran


def test_logout_falls_back_to_the_bare_login_state(configured_auth):
    at = AppTest.from_file(APP, default_timeout=60)
    # stand in for an established cookie session
    at.session_state["authentication_status"] = True
    at.session_state["username"] = "analyst"
    at.session_state["name"] = "Analyst"
    at.run()
    assert not at.exception
    assert [b.label for b in at.sidebar.button] == ["Logout"]   # full app is up
    assert at.title[0].value == "Overview"

    at.sidebar.button[0].click().run()      # click "Logout"

    assert not at.exception
    assert at.session_state["authentication_status"] is None
    # back to the bare login screen: no nav, no sidebar chrome, no page title
    assert at.sidebar.button == []
    assert at.sidebar.toggle == []
    assert at.title == []
    assert [ti.label for ti in at.text_input] == ["Username", "Password"]
