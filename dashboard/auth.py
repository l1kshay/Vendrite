"""
Login gate for the dashboard.

Credentials come only from ``VENDRITE_AUTH_*`` environment variables; the
password is stored as a bcrypt hash, never plaintext. ``require_login`` is
called once from the entry script (``app.py``) before ``st.navigation`` runs,
so every page is behind it.
"""

from __future__ import annotations

import streamlit as st
import streamlit_authenticator as stauth

from config import settings

_AUTH_ENV = {
    "VENDRITE_AUTH_COOKIE_KEY": settings.AUTH_COOKIE_KEY,
    "VENDRITE_AUTH_USERNAME": settings.AUTH_USERNAME,
    "VENDRITE_AUTH_NAME": settings.AUTH_NAME,
    "VENDRITE_AUTH_PASSWORD_HASH": settings.AUTH_PASSWORD_HASH,
}


def _build_authenticator() -> stauth.Authenticate:
    credentials = {
        "usernames": {
            settings.AUTH_USERNAME: {
                "name": settings.AUTH_NAME,
                "email": settings.AUTH_EMAIL or "",
                "password": settings.AUTH_PASSWORD_HASH,  # already a bcrypt hash
            }
        }
    }
    return stauth.Authenticate(
        credentials,
        settings.AUTH_COOKIE_NAME,
        settings.AUTH_COOKIE_KEY,
        settings.AUTH_COOKIE_EXPIRY_DAYS,
        auto_hash=False,
    )


def require_login() -> str:
    """Render the login gate. Returns the username, or halts the script."""
    missing = [name for name, value in _AUTH_ENV.items() if not value]
    if missing:
        st.error(
            "Dashboard authentication is not configured. Set these environment "
            "variables (see `.env.example`): " + ", ".join(missing)
        )
        st.stop()

    authenticator = _build_authenticator()
    authenticator.login(location="main")
    status = st.session_state.get("authentication_status")
    if status is False:
        st.error("Incorrect username or password.")
        st.stop()
    if status is None:
        st.info("Please sign in to view the dashboard.")
        st.stop()

    authenticator.logout(location="sidebar")
    st.sidebar.caption(f"Signed in as {st.session_state.get('name')}")
    return st.session_state.get("username")
