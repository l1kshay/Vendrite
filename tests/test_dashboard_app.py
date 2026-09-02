"""
Headless tests for the dashboard entry script (``dashboard/app.py``), driven by
``streamlit.testing.v1.AppTest``. No browser, no DB.

The important invariant here is structural: ``st.navigation`` must be called on
*every* run — with the real page tree when signed in, and with a hidden
one-page nav when signed out. That hidden call is what replaces a sidebar nav
the browser is still holding from an earlier authenticated run; skipping it (an
early ``st.stop()``) is what left the full nav on the login screen. AppTest
can't inspect the browser's retained widgets, so we assert the invariant at the
source: spy on ``st.navigation`` and check how it was called.
"""

from __future__ import annotations

from pathlib import Path

import bcrypt
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import dashboard.auth as auth
from config import settings

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
_HASH = bcrypt.hashpw(b"test-pw", bcrypt.gensalt()).decode()


@pytest.fixture
def configured_auth(monkeypatch):
    """Make the auth gate believe it is configured, without a real ``.env``."""
    for name, value in {
        "AUTH_USERNAME": "analyst",
        "AUTH_NAME": "Analyst",
        "AUTH_EMAIL": "analyst@example.com",
        "AUTH_PASSWORD_HASH": _HASH,
        "AUTH_COOKIE_NAME": "vendrite_auth",
        "AUTH_COOKIE_KEY": "test-cookie-key",
        "AUTH_COOKIE_EXPIRY_DAYS": 7,
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    # _AUTH_ENV is frozen at import time from the above; rebuild it non-empty
    monkeypatch.setattr(auth, "_AUTH_ENV", {k: "set" for k in auth._AUTH_ENV}, raising=False)


@pytest.fixture
def nav_spy(monkeypatch):
    """Record every ``st.navigation`` call the script makes."""
    real = st.navigation
    calls: list[dict] = []

    def spy(pages, **kwargs):
        calls.append({"pages": pages, "kwargs": kwargs})
        return real(pages, **kwargs)

    monkeypatch.setattr(st, "navigation", spy)
    return calls


def test_unauthenticated_render_is_only_the_login_card(configured_auth, nav_spy):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()

    assert not at.exception
    # the login form, and nothing that belongs to the authenticated app
    assert [ti.label for ti in at.text_input] == ["Username", "Password"]
    assert at.sidebar.button == []
    assert at.sidebar.toggle == []          # no "Presentation mode"
    assert at.title == []                   # no page rendered

    # the invariant: navigation was still called, hidden, so the browser drops
    # any nav retained from a previous signed-in run
    assert len(nav_spy) == 1
    assert nav_spy[0]["kwargs"].get("position") == "hidden"


def test_authenticated_build_uses_the_real_page_tree(configured_auth, nav_spy):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["authentication_status"] = True
    at.session_state["username"] = "analyst"
    at.session_state["name"] = "Analyst"
    at.run()

    assert not at.exception
    assert [b.label for b in at.sidebar.button] == ["Logout"]
    assert [t.label for t in at.sidebar.toggle] == ["Presentation mode"]
    assert at.title[0].value == "Overview"

    assert len(nav_spy) == 1
    assert nav_spy[0]["kwargs"].get("position", "sidebar") == "sidebar"
    assert set(nav_spy[0]["pages"]) == {"Analytics", "Models"}


def test_logout_falls_back_to_the_bare_login_state(configured_auth, nav_spy):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["authentication_status"] = True
    at.session_state["username"] = "analyst"
    at.session_state["name"] = "Analyst"
    at.run()
    assert [b.label for b in at.sidebar.button] == ["Logout"]

    at.sidebar.button[0].click().run()      # click "Logout"

    assert not at.exception
    assert at.session_state["authentication_status"] is None
    # bare login screen: no nav, no sidebar chrome, no page title
    assert at.sidebar.button == []
    assert at.sidebar.toggle == []
    assert at.title == []
    assert [ti.label for ti in at.text_input] == ["Username", "Password"]
    # and the final run still called navigation (hidden) rather than stopping
    # before it — this is the regression guard
    assert nav_spy[-1]["kwargs"].get("position") == "hidden"
