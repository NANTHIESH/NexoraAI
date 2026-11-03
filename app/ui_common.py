"""Shared Streamlit helpers: API client, login sidebar, status banner."""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

from nexora.config import load_config

API_URL = os.environ.get("NEXORA_API_URL", load_config().api.base_url)
TIMEOUT_SECONDS = 180  # the first answer after start-up loads the models


class ApiError(Exception):
    """The API answered with an error status; ``status`` and ``detail`` explain it."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


@st.cache_resource
def _client() -> httpx.Client:
    """One pooled HTTP client for the whole Streamlit process (no new connection per call)."""
    return httpx.Client(timeout=TIMEOUT_SECONDS)


def api(method: str, path: str, headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
    """Call the Nexora API with the logged-in user's token; raises :class:`ApiError` on failure."""
    headers = dict(headers or {})
    if token := st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = _client().request(method, f"{API_URL}{path}", headers=headers, **kwargs)
    except httpx.ConnectError as exc:
        raise ApiError(0, f"The Nexora API is not running at {API_URL}. Start it with `make api` "
                          "(python -m uvicorn nexora.api.main:app --port 8000).") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise ApiError(resp.status_code, str(detail))
    return resp


CHAT_CSS = """
<style>
/* ChatGPT-like look: narrow centred column, no chrome, soft rounded shapes */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {display: none;}
header[data-testid="stHeader"] {background: transparent;}
.block-container {max-width: 48rem; padding-top: 2rem; padding-bottom: 7rem;}
[data-testid="stSidebar"] {border-right: 1px solid #ececea;}
[data-testid="stSidebar"] .stButton > button {width: 100%; justify-content: flex-start; border-radius: .75rem;}

/* messages: no avatars; user right in a grey bubble; assistant plain text */
[data-testid="stChatMessage"] {background: transparent; padding: .5rem 0; gap: 0;}
[data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] {display: none;}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {justify-content: flex-end;}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
    background: #efefed; border-radius: 1.4rem; padding: .7rem 1.2rem !important; max-width: 80%;
    flex: 0 1 auto !important; width: fit-content !important; min-width: 0;
    margin: 0 0 0 auto !important; height: auto !important;}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) p {margin: 0;}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stMarkdownContainer"] {
    margin-bottom: 0 !important;}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stVerticalBlock"] {gap: 0;}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) [data-testid="stChatMessageContent"] {
    max-width: 100%; line-height: 1.65;}

/* quiet icon buttons under an answer */
.stButton > button[kind="tertiary"] {color: #6b6b6b; padding: .25rem .5rem; border-radius: .5rem;}
.stButton > button[kind="tertiary"]:hover {background: #efefed; color: #0d0d0d;}

/* expanders and alerts blend in */
[data-testid="stExpander"] {border: 1px solid #ececea; border-radius: .9rem; background: transparent;}

/* the message box: one big rounded pill */
[data-testid="stChatInput"] {border-radius: 1.6rem; background: #f4f4f2; border: 1px solid #e3e3e0;
    box-shadow: 0 2px 8px rgba(0,0,0,.05);}
[data-testid="stChatInput"] textarea {background: transparent;}
[data-testid="stBottom"] > div {background: #fcfcfb;}

/* welcome screen */
.welcome {text-align: center; margin: 14vh 0 1.5rem; font-size: 2rem; font-weight: 600; letter-spacing: -.01em;}
.welcome-sub {text-align: center; color: #6b6b6b; margin-bottom: 1.5rem;}
.st-key-suggestions .stButton > button {border-radius: 1rem; border: 1px solid #e3e3e0; background: transparent;
    text-align: left; justify-content: flex-start; padding: .8rem 1rem; height: 100%; min-height: 4.2rem;}
.st-key-suggestions .stButton > button:hover {background: #f4f4f2;}
</style>
"""


def apply_chat_style() -> None:
    """Inject the ChatGPT-like stylesheet."""
    st.markdown(CHAT_CSS, unsafe_allow_html=True)


@st.cache_data(ttl=15, show_spinner=False)
def _health() -> dict[str, Any] | str:
    """API health, cached for 15 s so normal clicks do not ping the API (and Ollama) every rerun."""
    try:
        return api("GET", "/health").json()
    except ApiError as exc:
        return exc.detail


def show_health_banner() -> None:
    """Warn when the language model is unavailable (extractive mode)."""
    health = _health()
    if isinstance(health, str):
        _health.clear()  # do not cache an outage
        st.error(health)
        st.stop()
    if not health["llm_ready"]:
        st.warning(f"**Extractive mode.** {health['notice']} Install and start Ollama from "
                   "https://ollama.com, then run `make setup`.")


AUTH_CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {display: none;}
.auth-title {text-align: center; font-size: 2rem; font-weight: 600; letter-spacing: -.01em; margin: 8vh 0 .25rem;}
.auth-sub {text-align: center; color: #6b6b6b; margin-bottom: 1.5rem;}
</style>
"""


def _start_session(body: dict[str, Any]) -> None:
    st.session_state.update(token=body["token"], user=body["user"], messages=[])
    st.rerun()


def auth_page() -> None:
    """Full-page login / register screen (shown until someone is logged in)."""
    st.markdown(AUTH_CSS, unsafe_allow_html=True)
    st.markdown('<div class="auth-title">Nexora AI</div>'
                '<div class="auth-sub">Log in or create an account to continue.</div>', unsafe_allow_html=True)
    login_tab, register_tab = st.tabs(["Log in", "Register"])
    with login_tab, st.form("login_form"):
        who = st.text_input("Email or employee ID", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        if st.form_submit_button("Log in", type="primary", use_container_width=True):
            if not who.strip() or not password:
                st.error("Please enter your email (or employee ID) and password.")
            else:
                try:
                    _start_session(api("POST", "/login", json={"employee_id": who.strip(), "password": password}).json())
                except ApiError as exc:
                    st.error(exc.detail)
        st.caption(f"Demo accounts: employee ID E001 to E0xx, password `{load_config().auth.demo_password}`.")
    with register_tab, st.form("register_form"):
        name = st.text_input("Full name", autocomplete="name")
        email = st.text_input("Email", autocomplete="email")
        password = st.text_input("Password (at least 8 characters)", type="password", autocomplete="new-password")
        confirm = st.text_input("Repeat password", type="password", autocomplete="new-password")
        if st.form_submit_button("Create account", type="primary", use_container_width=True):
            if not name.strip() or not email.strip() or not password:
                st.error("Please fill in all fields.")
            elif password != confirm:
                st.error("The passwords do not match.")
            else:
                try:
                    _start_session(api("POST", "/register",
                                       json={"name": name, "email": email, "password": password}).json())
                except ApiError as exc:
                    st.error(exc.detail)
        st.caption("New accounts get the Employee role (company documents and your own tasks).")


def require_login() -> dict[str, Any]:
    """Show the login/register page until someone is logged in; then the sidebar. Returns the user."""
    apply_chat_style()
    if "user" not in st.session_state:
        auth_page()
        st.stop()
    show_health_banner()
    with st.sidebar:
        st.subheader("Nexora AI")
        user = st.session_state["user"]
        if st.button("New chat", icon=":material/edit_square:", key="new_chat"):
            for key in ("messages", "session_id", "pending", "last_audio_id"):
                st.session_state.pop(key, None)
            st.session_state["messages"] = []
            st.rerun()
        st.caption(f"{user['name']} - {user['job_title']} ({user['role']})")
        if st.button("Log out", icon=":material/logout:"):
            for key in ("token", "user", "messages", "session_id", "last_audio_id", "rated", "pending"):
                st.session_state.pop(key, None)
            st.rerun()
    return st.session_state["user"]
