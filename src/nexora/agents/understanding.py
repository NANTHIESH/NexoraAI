"""Language understanding: one model call turns a (possibly vague, informal or follow-up) message into
an intent, a clean standalone question, or a clarifying question with options.

The model only decides *what is being asked*. Amounts, counts and facts are still computed from the data
by code, never written by the model. Any failure returns ``None`` so the caller falls back to the
keyword router and the rule-based question rewriting.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Protocol

from nexora.agents.memory import ANSWER_CHARS_IN_PROMPT, MAX_REWRITE_EXTRA_CHARS, Turn
from nexora.agents.router import INTENTS

log = logging.getLogger("nexora.understanding")

CLARIFY = "clarify"
MAX_OPTIONS = 3
MAX_OPTION_CHARS = 120

UNDERSTAND_PROMPT = """You are the language-understanding step of a workplace assistant for Nexora Software Ltd.
Read the user's last message (and the conversation, if there is one) and reply with JSON with these keys:
"intent": one of
- "knowledge": company rules, policies, documents (leave, remote work, expenses, security, deployment...)
- "report": weekly, monthly or yearly reports, income, expenses, spending, profit, cash, headcount, or the status of project tasks
- "subscription": software subscriptions and tools the company pays for: how many, cost, usage, duplicates, price increases, unused or overlapping tools, savings
- "smalltalk": greetings, thanks, or questions about the assistant
- "clarify": ONLY if the message is too vague to tell which of the above is wanted and the conversation does not help
"question": the message rewritten as ONE clear, standalone question in English. Fix typos, expand short or informal wording, and replace words like it, that or they with what they refer to in the conversation. Keep tool names, numbers, months and years exactly. Do not answer it and do not add facts. If it is already clear, repeat it unchanged.
"clarification": for "clarify" only: one short, friendly question asking what they mean. Otherwise null.
"options": for "clarify" only: 2 or 3 complete questions the user could pick, written the way the user would ask them. Otherwise [].

Examples
Message: whats the damage this month money wise
{"intent": "report", "question": "Give me the monthly report", "clarification": null, "options": []}
Message: how many subs do we hv
{"intent": "subscription", "question": "How many subscriptions do we have?", "clarification": null, "options": []}
Message: can i work from home on fridays
{"intent": "knowledge", "question": "Can I work from home on Fridays?", "clarification": null, "options": []}
Conversation: User: What does Datadog cost? / Assistant: Datadog costs 120 EUR per month.
Message: and slack?
{"intent": "subscription", "question": "What does Slack cost?", "clarification": null, "options": []}
Message: how are we doing?
{"intent": "clarify", "question": "How are we doing?", "clarification": "Happy to help! Do you mean money, project progress or software costs?", "options": ["Give me the monthly report", "How many tasks are blocked?", "Which subscriptions can we cancel?"]}
Message: thanks a lot!
{"intent": "smalltalk", "question": "Thanks a lot!", "clarification": null, "options": []}
Output JSON only."""


class SupportsChat(Protocol):
    """Anything with the OllamaClient.chat signature."""

    def chat(self, system: str, user: str, json_mode: bool = False) -> str: ...


@dataclass(frozen=True)
class Understanding:
    """What the model made of a message."""

    intent: str                      # knowledge | report | subscription | smalltalk | clarify
    question: str                    # clear standalone version of the message (the original if unchanged)
    clarification: str | None = None
    options: list[str] = field(default_factory=list)


def parse_understanding(text: str, original: str) -> Understanding | None:
    """Validate the model's JSON; ``None`` if it is unusable (the caller then uses the keyword fallback)."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    intent = data.get("intent")
    if intent not in (*INTENTS, CLARIFY):
        return None
    question = data.get("question")
    if not isinstance(question, str) or not question.strip() \
            or len(question) > len(original) + MAX_REWRITE_EXTRA_CHARS:
        question = original  # an empty or runaway rewrite is never trusted
    question = " ".join(question.split())
    if intent != CLARIFY:
        return Understanding(intent, question)
    clarification = data.get("clarification")
    raw_options = data.get("options")
    options = [" ".join(o.split()) for o in raw_options if isinstance(o, str) and o.strip()
               and len(o) <= MAX_OPTION_CHARS][:MAX_OPTIONS] if isinstance(raw_options, list) else []
    if not isinstance(clarification, str) or not clarification.strip() or len(options) < 2:
        return None  # a clarifying question needs wording and at least two choices
    return Understanding(CLARIFY, question, " ".join(clarification.split()), options)


def understand(question: str, history: list[Turn], who: str, llm: SupportsChat) -> Understanding | None:
    """Ask the model what ``question`` means. ``who`` describes the user, e.g. ``Ruth, CEO (role: owner)``."""
    conversation = "\n".join(f"User: {t.question}\nAssistant: {t.answer[:ANSWER_CHARS_IN_PROMPT]}" for t in history)
    prompt = (f"The user is {who}.\n" + (f"Conversation:\n{conversation}\n" if conversation else "")
              + f"Message: {question}")
    try:
        reply = llm.chat(UNDERSTAND_PROMPT, prompt, json_mode=True)
    except Exception as exc:  # noqa: BLE001 - Ollama missing, timeout, model not pulled...
        log.info("Understanding failed (%s); using the keyword fallback", type(exc).__name__)
        return None
    understood = parse_understanding(reply, question)
    log.info("understood intent=%s", understood.intent if understood else None)
    return understood
