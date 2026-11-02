"""Nexora AI - chat page.

Run: python -m streamlit run app/Home.py   (or: make ui; the API must be running)
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from ui_common import ApiError, api, require_login

st.set_page_config(page_title="Nexora AI", page_icon=None, layout="centered")
user = require_login()

st.title("Nexora AI")
st.caption("An intelligent business assistant to search, manage and act, powered by RAG and AI Agents.")

with st.expander("Example questions"):
    st.markdown("- How many vacation days do full-time employees get?\n- Can we deploy to production on a Friday afternoon?\n"
                "- Give me the monthly report *(Owner / Operations Manager)*\n"
                "- Which subscriptions can we cancel? *(Owner / Operations Manager)*")


def send_feedback(chat_id: int, rating: int) -> None:
    """Callback for the thumbs buttons."""
    try:
        api("POST", "/feedback", json={"chat_id": chat_id, "rating": rating})
        st.session_state.setdefault("rated", set()).add(chat_id)
    except ApiError as exc:
        st.session_state["feedback_error"] = exc.detail


def render_sources(msg: dict[str, Any]) -> None:
    """Citation expanders: one per retrieved passage."""
    sources = msg.get("sources") or []
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})"):
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


def render_assistant(msg: dict[str, Any], key: int) -> None:
    """One assistant message with warnings, sources, feedback and read-aloud."""
    if msg.get("denied"):
        st.error(msg["text"])
        return
    if msg.get("refused"):
        st.info(msg["text"])
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
    st.caption(f"Intent: {msg['intent']} ({msg['method']})")
    cols = st.columns([1, 1, 2, 6])
    chat_id = msg["chat_id"]
    rated = chat_id in st.session_state.get("rated", set())
    cols[0].button("Helpful", key=f"up{key}", disabled=rated, on_click=send_feedback, args=(chat_id, 1))
    cols[1].button("Not helpful", key=f"down{key}", disabled=rated, on_click=send_feedback, args=(chat_id, -1))
    if cols[2].button("Read aloud", key=f"tts{key}"):
        try:
            st.audio(api("POST", "/voice/tts", json={"text": msg["text"]}).content, format="audio/wav", autoplay=True)
        except ApiError as exc:
            st.warning(f"Read aloud is not available: {exc.detail}")


for i, message in enumerate(st.session_state.get("messages", [])):
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(message["text"])
        else:
            render_assistant(message, i)

if error := st.session_state.pop("feedback_error", None):
    st.error(error)

if question := st.chat_input("Ask about company rules, reports or subscriptions"):
    st.session_state["messages"].append({"role": "user", "text": question})
    try:
        with st.spinner("Thinking..."):
            answer = api("POST", "/chat", json={"question": question}).json()
    except ApiError as exc:
        answer = {"text": exc.detail, "denied": True, "intent": "error", "method": "-", "chat_id": -1}
    st.session_state["messages"].append({"role": "assistant", **answer})
    st.rerun()
