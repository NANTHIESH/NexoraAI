"""Intent router: Ollama JSON classification with a keyword fallback."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

INTENTS = ("knowledge", "report", "subscription", "smalltalk")
log = logging.getLogger("nexora.router")

ROUTER_PROMPT = """Classify the user's message into exactly one intent:
- knowledge: a question about company rules, policies or documents (leave, remote work, expenses, security, deployment...)
- report: a request for a weekly, monthly or yearly report, income, expenses, cash, profit, or the status of project tasks (done, blocked or overdue tasks)
- subscription: a question about software subscriptions, tools we pay for, duplicate charges, price increases, unused or overlapping tools, savings
- smalltalk: greetings, thanks, or questions about the assistant itself
Answer with JSON only: {"intent": "<knowledge|report|subscription|smalltalk>"}"""

KEYWORDS = {
    "subscription": r"subscri|licen[cs]e|saving|cancel|duplicate|double[- ]charge|overlap|unused|"
                    r"price (increase|rise|hike)|software (cost|tool|spend)|\btools?\b.*\b(pay|cost|use)|"
                    r"\b(pay|paying) for\b.*\btools?\b|\btwo\b.*\btools?\b",
    "report": r"\breport\b|income|revenue|expenses?\b|profit|\bnet\b|cash ?flow|spending|\bspen[dt]\b|"
              r"weekly|monthly|yearly|annual|overdue|\btasks?\b.*\b(blocked|done|status)\b|\b(blocked|done)\b.*\btasks?\b",
    "smalltalk": r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks?|thank you|bye|goodbye)\b|"
                 r"who are you|what can you do|how are you",
}


class SupportsChat(Protocol):
    """Anything with the OllamaClient.chat signature."""

    def chat(self, system: str, user: str, json_mode: bool = False) -> str: ...


@dataclass(frozen=True)
class Route:
    """Chosen intent and how it was decided (``llm`` or ``keywords``)."""

    intent: str
    method: str


def keyword_route(question: str) -> str:
    """Rule-based intent used when the model is unavailable or answers badly."""
    for intent in ("smalltalk", "subscription", "report"):
        if re.search(KEYWORDS[intent], question, re.IGNORECASE):
            return intent
    return "knowledge"


def route(question: str, llm: SupportsChat | None = None) -> Route:
    """Classify ``question``; always returns a valid intent and logs the choice."""
    result = None
    if llm is not None:
        try:
            intent = json.loads(llm.chat(ROUTER_PROMPT, question, json_mode=True)).get("intent")
            if intent in INTENTS:
                result = Route(intent, "llm")
        except Exception as exc:  # noqa: BLE001 - any model/network/JSON failure falls back
            log.info("LLM routing failed (%s); using keywords", type(exc).__name__)
    result = result or Route(keyword_route(question), "keywords")
    log.info("intent=%s method=%s", result.intent, result.method)
    return result
