"""Follow-up questions to offer after an answer (rule-based, so they always work and always fit the role)."""

from __future__ import annotations

from nexora.security.rbac import Permission, can

MAX_SUGGESTIONS = 3

# starter questions per role (the same ones the chat's welcome screen shows)
_DOCUMENTS = ["How many vacation days do full-time employees get?", "Can we deploy to production on a Friday afternoon?"]
_FINANCE = ["Give me the monthly report", "Which subscriptions can we cancel?", "How much did we spend on GitHub last month?"]
_TASKS = ["How many tasks are blocked?", "Which tasks are overdue?"]

# (question, permission needed to ask it)
_SUB_LIST = ("Which subscriptions can we cancel?", Permission.SUBSCRIPTIONS)
_SUB_COUNT = ("How many subscriptions do we have?", Permission.SUBSCRIPTIONS)
_SUB_UNUSED = ("Which tools are unused?", Permission.SUBSCRIPTIONS)
_SUB_DUPES = ("Any duplicate charges?", Permission.SUBSCRIPTIONS)
_SUB_PRICES = ("Any price increases?", Permission.SUBSCRIPTIONS)
_REPORT_MONTH = ("Give me the monthly report", Permission.FINANCE)
_REPORT_YEAR = ("Give me the yearly report", Permission.FINANCE)
_BIGGEST = ("What is our biggest expense?", Permission.FINANCE)
_BLOCKED = ("How many tasks are blocked?", Permission.OWN_TASKS)
_OVERDUE = ("Which tasks are overdue?", Permission.OWN_TASKS)


def starters(role: str) -> list[str]:
    """Questions a role can ask right away."""
    return (_DOCUMENTS[:1] + _FINANCE) if can(role, Permission.FINANCE) else (_DOCUMENTS + _TASKS)


def follow_ups(role: str, question: str, *, intent: str, kind: str | None, answered: bool) -> list[str]:
    """Up to three next questions that suit what was just answered and that ``role`` is allowed to ask.

    ``answered`` is False for refusals, denials and "I could not match that" replies: those get the starter
    questions instead, so the user always has something to click.
    """
    if not answered or intent == "smalltalk":
        return starters(role)[:MAX_SUGGESTIONS]
    if intent == "subscription":
        picks = ([_SUB_LIST, _SUB_UNUSED, _SUB_DUPES] if kind == "inventory"
                 else [_SUB_LIST, _SUB_COUNT] if kind == "tool_facts"
                 else [_SUB_COUNT, _SUB_UNUSED, _SUB_DUPES, _SUB_PRICES])
    elif intent == "report":
        picks = ([_OVERDUE, _BLOCKED] if kind in ("tasks", "headcount")
                 else [_REPORT_MONTH, _REPORT_YEAR, _BIGGEST, _SUB_LIST])
        if not can(role, Permission.FINANCE) and kind not in ("tasks", "headcount"):
            picks = [_OVERDUE, _BLOCKED]
    else:
        return []
    asked = " ".join(question.lower().split())
    return [q for q, need in picks if can(role, need) and " ".join(q.lower().split()) != asked][:MAX_SUGGESTIONS]
