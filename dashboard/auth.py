"""
Login gate for the dashboard.

Credentials come only from ``VENDRITE_AUTH_*`` environment variables; the
password is stored as a bcrypt hash, never plaintext.

Split into three calls the entry script drives in order:

* ``authenticate()``      — mount the cookie, return whether the user is in.
                            Renders nothing.
* ``render_login_form()`` — the centred login card (main area). Call only
                            when ``authenticate()`` returned ``False``.
* ``render_logout()``     — the sidebar "Log out" button + identity caption.
                            Call only when authenticated.

Why the split: ``st.navigation`` transmits the sidebar nav to the browser
*only on runs where it is called*, and the browser keeps showing the last one
it received. So the entry script must call ``st.navigation`` on **every** run
— a hidden one-page nav when logged out — to clear a nav left over from a
previous authenticated run. That means the gate can't ``st.stop()`` on its
own; it reports state and lets ``app.py`` sequence the navigation call.
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

_AUTHENTICATOR_KEY = "_vd_authenticator"


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
    # constructing the model seeds st.session_state["authentication_status"] /
    # ["name"] / ["username"] / ["logout"] to None if absent — relied on below.
    return stauth.Authenticate(
        credentials,
        settings.AUTH_COOKIE_NAME,
        settings.AUTH_COOKIE_KEY,
        settings.AUTH_COOKIE_EXPIRY_DAYS,
        auto_hash=False,
    )


def _require_configured() -> None:
    missing = [name for name, value in _AUTH_ENV.items() if not value]
    if missing:
        st.error(
            "Dashboard authentication is not configured. Set these environment "
            "variables (see `.env.example`): " + ", ".join(missing),
            icon=":material/gpp_maybe:",
        )
        st.stop()


def authenticate() -> bool:
    """Mount the auth cookie and report whether the user is signed in.

    Renders nothing visible. Call once at the top of every run, before any
    ``st.navigation`` call.
    """
    _require_configured()
    authenticator = _build_authenticator()
    st.session_state[_AUTHENTICATOR_KEY] = authenticator
    # location="unrendered": process the re-auth cookie (or do nothing) without
    # drawing a form. Returns immediately when already authenticated.
    authenticator.login(location="unrendered")
    return st.session_state.get("authentication_status") is True


def render_login_form() -> None:
    """The centred product login card. Call only when not authenticated."""
    authenticator = st.session_state[_AUTHENTICATOR_KEY]
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


def render_logout() -> str:
    """Sidebar "Log out" button + "Signed in as …" caption. Returns the
    username. Call only when authenticated.

    On click, ``streamlit-authenticator`` clears the session but does not
    rerun, so we do — the next run takes the unauthenticated path and its
    hidden ``st.navigation`` call clears the sidebar nav.
    """
    authenticator = st.session_state[_AUTHENTICATOR_KEY]
    with st.sidebar.container(key="vd-session"):
        authenticator.logout(location="main")
        if st.session_state.get("authentication_status") is not True:
            st.rerun()
        st.caption(f"Signed in as {st.session_state.get('name')}")
    return st.session_state.get("username")
