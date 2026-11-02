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


def api(method: str, path: str, headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
    """Call the Nexora API with the logged-in user's token; raises :class:`ApiError` on failure."""
    headers = dict(headers or {})
    if token := st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.request(method, f"{API_URL}{path}", headers=headers, timeout=TIMEOUT_SECONDS, **kwargs)
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


def show_health_banner() -> None:
    """Warn when the language model is unavailable (extractive mode)."""
    try:
        health = api("GET", "/health").json()
    except ApiError as exc:
        st.error(exc.detail)
        st.stop()
    if not health["llm_ready"]:
        st.warning(f"**Extractive mode.** {health['notice']} Install and start Ollama from "
                   "https://ollama.com, then run `make setup`.")


def require_login() -> dict[str, Any]:
    """Render the login sidebar; stop the page until someone is logged in. Returns the user."""
    apply_chat_style()
    show_health_banner()
    with st.sidebar:
        st.subheader("Nexora AI")
        if "user" in st.session_state:
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
        else:
            users = api("GET", "/users").json()
            labels = {f"{u['name']} - {u['job_title']} ({u['role']})": u["employee_id"] for u in users}
            choice = st.selectbox("Who are you?", list(labels))
            password = st.text_input("Password", type="password",
                                     help=f"Demo password: {load_config().auth.demo_password}")
            if st.button("Log in", type="primary"):
                try:
                    body = api("POST", "/login", json={"employee_id": labels[choice], "password": password}).json()
                except ApiError as exc:
                    st.error(exc.detail)
                else:
                    st.session_state.update(token=body["token"], user=body["user"], messages=[])
                    st.rerun()
    if "user" not in st.session_state:
        st.info("Choose a user in the sidebar and log in. All data in this demo is synthetic.")
        st.stop()
    return st.session_state["user"]

