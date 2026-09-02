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
            "variables (see `.env.example`): " + ", ".join(missing),
            icon=":material/gpp_maybe:",
        )
        st.stop()

    authenticator = _build_authenticator()

    if st.session_state.get("authentication_status") is not True:
        # centred product login: wordmark, then the form as a card in a
        # narrow middle column
        st.markdown(
            '<div class="vd-login-head"><span class="vd-wordmark">Vendrite</span></div>'
            '<div class="vd-login-sub">Sales &amp; customer analytics</div>',
            unsafe_allow_html=True,
        )
        mid = st.columns([1, 1.2, 1])[1]
        with mid:
            authenticator.login(location="main")
            status = st.session_state.get("authentication_status")
            if status is False:
                st.error("Incorrect username or password.", icon=":material/lock:")
            elif status is None:
                st.caption("Enter your credentials to continue.")
        if st.session_state.get("authentication_status") is not True:
            st.stop()
    else:
        authenticator.login(location="main")  # keeps the cookie component mounted

    authenticator.logout(location="sidebar")
    if st.session_state.get("authentication_status") is not True:
        # The logout button was just clicked. streamlit-authenticator clears the
        # session here but does NOT trigger a rerun, so without this the script
        # would fall through to `st.navigation()` in app.py and paint the full
        # app chrome beside the login card. Rerun now: the next run takes the
        # unauthenticated branch above and `st.stop()`s before any nav exists.
        st.rerun()
    st.sidebar.caption(f"Signed in as {st.session_state.get('name')}")
    return st.session_state.get("username")
