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


def api(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Call the Nexora API with the logged-in user's token; raises :class:`ApiError` on failure."""
    headers = {}
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
    show_health_banner()
    with st.sidebar:
        st.header("Nexora AI")
        if "user" in st.session_state:
            user = st.session_state["user"]
            st.write(f"**{user['name']}**  \n{user['job_title']} ({user['role']})")
            if st.button("Log out"):
                for key in ("token", "user", "messages"):
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
