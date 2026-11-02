"""Conversation memory: last N turns and question rewriting for follow-up questions."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Protocol

FOLLOW_UP_WORDS = re.compile(
    r"\b(it|its|that|this|those|these|they|them|their|there|he|she|him|her|same|before)\b"
    r"|^\s*(and|also|but|so|what about|how about)\b", re.IGNORECASE)
SHORT_QUESTION_WORDS = 8
ANSWER_CHARS_IN_PROMPT = 200
MAX_REWRITE_EXTRA_CHARS = 200

REWRITE_PROMPT = """Rewrite the user's last message as ONE standalone question that can be understood without the conversation.
Replace references such as 'it', 'that', 'before' or 'the same' with the topic from the conversation.
Do not answer it. If the message is already standalone, repeat it unchanged. Output only the question.

Example 1
Conversation:
User: How long is the notice period for the garden?
Assistant: Two weeks.
Last message: And what was it before?
Standalone question: What was the notice period for the garden before?

Example 2
Conversation:
User: Give me the daily boat report
Assistant: Three boats sailed.
Last message: What about the whole year?
Standalone question: Give me the yearly boat report"""


class SupportsChat(Protocol):
    """Anything with the OllamaClient.chat signature."""

    def chat(self, system: str, user: str, json_mode: bool = False) -> str: ...


@dataclass(frozen=True)
class Turn:
    """One question and the assistant's answer (both already PII-redacted in the database)."""

    question: str
    answer: str


def load_history(conn: sqlite3.Connection, user_id: str, session_id: str, turns: int) -> list[Turn]:
    """The last ``turns`` question/answer pairs of this user's session, oldest first."""
    rows = conn.execute("SELECT role, content FROM chat_history WHERE user_id = ? AND session_id = ? "
                        "ORDER BY id DESC LIMIT ?", (user_id, session_id, turns * 2)).fetchall()
    history: list[Turn] = []
    pending: str | None = None
    for row in reversed(rows):
        if row["role"] == "user":
            pending = row["content"]
        elif pending is not None:
            history.append(Turn(pending, row["content"]))
            pending = None
    return history[-turns:]


def looks_like_follow_up(question: str, history: list[Turn]) -> bool:
    """True if there is history and the question is short or refers back to it."""
    return bool(history) and (len(question.split()) <= SHORT_QUESTION_WORDS
                              or bool(FOLLOW_UP_WORDS.search(question)))


def _fallback(question: str, history: list[Turn]) -> str:
    """Without a model: prepend the previous question, but only if this one refers back to it."""
    return f"{history[-1].question} {question}" if FOLLOW_UP_WORDS.search(question) else question


def rewrite_question(question: str, history: list[Turn], llm: SupportsChat | None) -> str:
    """Standalone version of a follow-up question; the original if it is not a follow-up."""
    if not looks_like_follow_up(question, history):
        return question
    if llm is None:
        return _fallback(question, history)
    conversation = "\n".join(f"User: {t.question}\nAssistant: {t.answer[:ANSWER_CHARS_IN_PROMPT]}"
                             for t in history)
    prompt = f"Conversation:\n{conversation}\nLast message: {question}\nStandalone question:"
    try:
        text = llm.chat(REWRITE_PROMPT, prompt)
    except Exception:  # noqa: BLE001 - Ollama missing, model not pulled, timeouts...
        return _fallback(question, history)
    lines = text.strip().splitlines()
    rewritten = lines[0].strip().strip("\"' ") if lines else ""
    if not rewritten or len(rewritten) > len(question) + MAX_REWRITE_EXTRA_CHARS:
        return _fallback(question, history)
    return rewritten
