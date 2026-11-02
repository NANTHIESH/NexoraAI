"""Nexora AI - chat page.

Run: python -m streamlit run app/Home.py   (or: make ui; the API must be running)
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import streamlit as st

from ui_common import ApiError, api, require_login

st.set_page_config(page_title="Nexora AI", page_icon=None, layout="centered")
user = require_login()

DOCUMENT_STARTERS = ["How many vacation days do full-time employees get?",
                     "Can we deploy to production on a Friday afternoon?"]
FINANCE_STARTERS = ["Give me the monthly report", "Which subscriptions can we cancel?",
                    "How much did we spend on GitHub last month?"]
TASK_STARTERS = ["How many tasks are blocked?", "Which tasks are overdue?"]
WORD_DELAY_SECONDS = 0.02
MAX_TYPING_SECONDS = 2.5


def suggestions_for(role: str) -> list[str]:
    """Four starter questions that this role is allowed to ask."""
    if role in ("owner", "ops_manager"):
        return DOCUMENT_STARTERS[:1] + FINANCE_STARTERS
    return DOCUMENT_STARTERS + TASK_STARTERS


def send_feedback(chat_id: int, rating: int) -> None:
    """Callback for the thumbs buttons."""
    try:
        api("POST", "/feedback", json={"chat_id": chat_id, "rating": rating})
        st.session_state.setdefault("rated", set()).add(chat_id)
    except ApiError as exc:
        st.session_state["feedback_error"] = exc.detail


def pick(question: str) -> None:
    """Callback for the starter-question buttons."""
    st.session_state["pending"] = question


def typing(text: str) -> Iterator[str]:
    """Yield the answer word by word, like a streaming chat (total time is capped)."""
    words = text.split(" ")
    delay = min(WORD_DELAY_SECONDS, MAX_TYPING_SECONDS / max(len(words), 1))
    for word in words:
        yield word + " "
        time.sleep(delay)


def render_sources(msg: dict[str, Any]) -> None:
    """Citation expanders: one per retrieved passage."""
    sources = msg.get("sources") or []
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})", icon=":material/menu_book:"):
        for s in sources:
            tag = "" if s["status"] == "current" else " - SUPERSEDED (outdated)"
            st.markdown(f"**[{s['title']}, {s['section']}]**{tag}  \nversion {s['version']}, "
                        f"match score {s['score']:.2f}, file `{s['source']}`")
            st.text(s["text"])


def render_extras(msg: dict[str, Any]) -> None:
    """Report numbers and recommendations attached to an answer."""
    report = msg.get("report")
    if report and "income" in report:
        c1, c2, c3 = st.columns(3)
        c1.metric("Income", f"{report['income']:,.0f} EUR")
        c2.metric("Expenses", f"{report['expenses']:,.0f} EUR")
        c3.metric("Net", f"{report['net']:,.0f} EUR")
    if report and report.get("blocked_tasks"):
        with st.expander(f"Blocked tasks ({len(report['blocked_tasks'])} shown)"):
            st.dataframe(report["blocked_tasks"], hide_index=True)
    analysis = msg.get("analysis")
    if analysis:
        with st.expander(f"All {len(analysis['recommendations'])} recommendations (confirm actions on the Dashboard)"):
            st.dataframe([{"Recommendation": r["text"]} for r in analysis["recommendations"]], hide_index=True)


def render_assistant(msg: dict[str, Any], key: int, animate: bool) -> None:
    """One assistant message with warnings, sources, feedback and read-aloud."""
    if msg.get("denied"):
        st.error(msg["text"])
        return
    if msg.get("refused"):
        st.info(msg["text"])
    elif animate:
        st.write_stream(typing(msg["text"]))
    else:
        st.markdown(msg["text"])
    if msg.get("notice"):
        st.caption(msg["notice"])
    if msg.get("warning"):
        st.warning(msg["warning"])
    if msg.get("injection_flagged"):
        st.warning("Instruction-like text was found and removed from: " + ", ".join(msg["injection_flagged"]))
    render_extras(msg)
    render_sources(msg)
    if msg.get("rewritten_question"):
        st.caption(f"Understood as: {msg['rewritten_question']}")
    chat_id = msg["chat_id"]
    rated = chat_id in st.session_state.get("rated", set())
    cols = st.columns([1, 1, 1, 9])
    cols[0].button("", key=f"up{key}", icon=":material/thumb_up:", type="tertiary", help="Helpful",
                   disabled=rated, on_click=send_feedback, args=(chat_id, 1))
    cols[1].button("", key=f"down{key}", icon=":material/thumb_down:", type="tertiary", help="Not helpful",
                   disabled=rated, on_click=send_feedback, args=(chat_id, -1))
    if cols[2].button("", key=f"tts{key}", icon=":material/volume_up:", type="tertiary", help="Read aloud"):
        try:
            st.audio(api("POST", "/voice/tts", json={"text": msg["text"]}).content, format="audio/wav", autoplay=True)
        except ApiError as exc:
            st.warning(f"Read aloud is not available: {exc.detail}")
    cols[3].caption(f"{msg['intent']} ({msg['method']})")


def ask(question: str) -> None:
    """Send a question to the API (with this login session's id so follow-ups work) and show the answer."""
    st.session_state.setdefault("messages", []).append({"role": "user", "text": question})
    session_id = st.session_state.setdefault("session_id", uuid.uuid4().hex)
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking..."):
                answer = api("POST", "/chat", json={"question": question, "session_id": session_id}).json()
        except ApiError as exc:
            answer = {"text": exc.detail, "denied": True, "intent": "error", "method": "-", "chat_id": -1}
    st.session_state["messages"].append({"role": "assistant", "animate": True, **answer})
    st.rerun()


with st.sidebar.expander("Ask by voice (optional)", icon=":material/mic:"):
    recording = st.audio_input("Record your question")
    if recording is not None and recording.file_id != st.session_state.get("last_audio_id"):
        st.session_state["last_audio_id"] = recording.file_id
        try:
            spoken = api("POST", "/voice/stt", content=recording.getvalue(),
                         headers={"content-type": "audio/wav"}).json()["text"]
        except ApiError as exc:
            st.warning(f"Voice input is not available: {exc.detail}")
        else:
            if spoken:
                st.session_state["pending"] = spoken
            else:
                st.warning("I could not hear anything. Please try again.")

pending = st.session_state.pop("pending", None)
messages = st.session_state.get("messages", [])
if not messages and not pending:
    st.markdown('<div class="welcome">What can I help with?</div>'
                '<div class="welcome-sub">Ask about company rules, reports or software subscriptions.</div>',
                unsafe_allow_html=True)
    with st.container(key="suggestions"):
        columns = st.columns(2)
        for n, text in enumerate(suggestions_for(user["role"])):
            columns[n % 2].button(text, key=f"sug{n}", on_click=pick, args=(text,), use_container_width=True)

for i, message in enumerate(messages):
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(message["text"])
        else:
            render_assistant(message, i, animate=message.pop("animate", False))

if error := st.session_state.pop("feedback_error", None):
    st.error(error)

if question := (pending or st.chat_input("Message Nexora AI")):
    ask(question)
