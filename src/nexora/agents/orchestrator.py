"""Chat orchestration: route the question, check permissions, run the right agent, log it."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexora.agents.knowledge import KnowledgeAgent
from nexora.agents.lookups import (
    CAPABILITIES,
    REPORT_WORDS,
    answer_headcount,
    answer_income,
    answer_ranking,
    answer_tasks,
    answer_tool_facts,
    lookup_kind,
    topic_of,
)
from nexora.agents.memory import load_history, rewrite_question
from nexora.agents.report import build_report, project_view, template_project_summary
from nexora.agents.router import Route, keyword_route, route
from nexora.agents.spending import answer_spending, is_spending_question
from nexora.agents.subscription import analyze_subscriptions
from nexora.agents.tools import format_eur, parse_period
from nexora.config import Config, load_config
from nexora.db.repositories import Frames, MissingDataError, frames_from_db
from nexora.llm.ollama_client import OllamaClient
from nexora.rag.generator import Assistant
from nexora.security import audit
from nexora.security.auth import User
from nexora.security.pii import redact
from nexora.security.rbac import Permission, can

SMALLTALK_REPLY = ("Hello! I can answer questions about Nexora's company rules (with sources), create "
                   "weekly, monthly or yearly reports, and look for waste in software subscriptions.")
MAX_LISTED_RECOMMENDATIONS = 8
PERIOD_WORDS = re.compile(r"\b(weekly|monthly|yearly|annual|week|month|year)\b", re.IGNORECASE)
TOPIC_LABELS = {"unused_subscription": "unused subscriptions", "duplicate_charge": "duplicate charges",
                "price_increase": "unexplained price increases", "overlap": "overlapping tools",
                "unlisted_charge": "charges from tools that are not on the subscription list"}


@dataclass
class ChatResult:
    """Everything the API returns for one chat message."""

    intent: str
    method: str
    text: str
    refused: bool = False
    denied: bool = False
    mode: str = "llm"
    citations: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    warning: str | None = None
    notice: str | None = None
    report: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    injection_flagged: list[str] = field(default_factory=list)
    rewritten_question: str | None = None
    chat_id: int | None = None


def _source_dicts(hits: list) -> list[dict[str, Any]]:
    return [{"title": h.chunk.doc_title, "section": h.chunk.section, "source": h.chunk.source,
             "version": h.chunk.version, "status": h.chunk.status, "page": h.chunk.page,
             "score": h.score, "text": h.chunk.text} for h in hits]


class Orchestrator:
    """Entry point used by the API for chat messages."""

    def __init__(self, cfg: Config | None = None, assistant: Assistant | None = None,
                 llm: OllamaClient | None = None) -> None:
        self.cfg = cfg or load_config()
        self.llm = llm or OllamaClient(self.cfg)
        self._assistant = assistant

    @property
    def knowledge(self) -> KnowledgeAgent:
        """Knowledge agent (created lazily so the API starts without an index)."""
        if self._assistant is None:
            self._assistant = Assistant(self.cfg, llm=self.llm)
        return KnowledgeAgent(self._assistant)

    def llm_ready(self) -> bool:
        """True if Ollama runs and the model is pulled."""
        return self.llm.is_ready()

    def chat(self, conn: sqlite3.Connection, user: User, question: str, session_id: str = "default",
             include_superseded: bool | None = None) -> ChatResult:
        """Answer one message for ``user``; stores history and an audit record."""
        ready = self.llm_ready()
        llm = self.llm if ready else None
        history = load_history(conn, user.employee_id, session_id, self.cfg.retrieval.history_turns)
        effective = question
        if keyword_route(question) != "smalltalk":  # greetings and thanks are never rewritten
            effective = rewrite_question(question, history, llm)
        chosen = route(effective, llm)
        frames = self._frames(conn)
        if chosen.intent != "smalltalk":  # amounts, counts and facts are always looked up, never summarised
            kind = lookup_kind(effective, frames)
            if kind == "tool_facts":
                chosen = Route("subscription", f"{chosen.method}+rule")
            elif kind or is_spending_question(effective):
                chosen = Route("report", f"{chosen.method}+rule")
        result = self._dispatch(conn, user, effective, chosen.intent, include_superseded, frames)
        result.intent, result.method = chosen.intent, chosen.method
        result.rewritten_question = effective if effective != question else None
        conn.execute("INSERT INTO chat_history (user_id, session_id, role, content, intent, created_at) "
                     "VALUES (?,?,?,?,?,?)",
                     (user.employee_id, session_id, "user", redact(question), chosen.intent, _now()))
        cur = conn.execute("INSERT INTO chat_history (user_id, session_id, role, content, intent, created_at) "
                           "VALUES (?,?,?,?,?,?)",
                           (user.employee_id, session_id, "assistant", redact(result.text),
                            chosen.intent, _now()))
        conn.commit()
        result.chat_id = int(cur.lastrowid)
        action = "chat_denied" if result.denied else ("chat_refused" if result.refused else "chat")
        audit.log_event(conn, user.employee_id, user.role, action, question=question, intent=chosen.intent,
                        sources=sorted({s["source"] for s in result.sources}),
                        detail=f"method={chosen.method} mode={result.mode}"
                               + (f" rewritten={result.rewritten_question}" if result.rewritten_question else ""))
        return result

    # ------------------------------------------------------------------ intents
    @staticmethod
    def _frames(conn: sqlite3.Connection) -> Frames | None:
        """All business data, or ``None`` when the database has not been loaded (documents still work)."""
        try:
            return frames_from_db(conn)
        except MissingDataError:
            return None

    def _dispatch(self, conn: sqlite3.Connection, user: User, question: str, intent: str,
                  include_superseded: bool | None, frames: Frames | None) -> ChatResult:
        if intent == "smalltalk":
            return ChatResult(intent, "", SMALLTALK_REPLY)
        if intent == "knowledge":
            return self._knowledge(question, include_superseded)
        if frames is None:
            raise MissingDataError("The business data is not loaded")
        if intent == "report":
            return self._report(user, question, frames)
        return self._subscription(user, question, frames)

    def _knowledge(self, question: str, include_superseded: bool | None) -> ChatResult:
        answer = self.knowledge.run(question, include_superseded)
        return ChatResult("knowledge", "", answer.text, refused=answer.refused, mode=answer.mode,
                          citations=answer.citations, sources=_source_dicts(answer.sources),
                          warning=answer.warning, notice=answer.notice,
                          injection_flagged=answer.injection_flagged)

    def _denied(self, user: User, what: str) -> ChatResult:
        text = (f"Your role ({user.role}) does not have access to {what}. "
                "Please ask the Owner or the Operations Manager.")
        return ChatResult("", "", text, denied=True)

    def _report(self, user: User, question: str, frames: Frames) -> ChatResult:
        kind = lookup_kind(question)
        if is_spending_question(question) or kind in ("ranking", "income"):
            if not can(user.role, Permission.FINANCE):
                return self._denied(user, "bank and spending data")
            if kind == "ranking":
                answer = answer_ranking(frames, question)
            elif kind == "income":
                answer = answer_income(frames, question)
            else:
                answer = answer_spending(frames, self.cfg, question)
            return ChatResult("report", "", answer.text, report=answer.data)
        if kind == "headcount":
            answer = answer_headcount(frames)
            return ChatResult("report", "", answer.text, report=answer.data)
        if kind == "tasks":
            if can(user.role, Permission.ALL_TASKS):
                scope = None
            elif can(user.role, Permission.OWN_TASKS):
                scope = user.employee_id
            else:
                return self._denied(user, "tasks")
            answer = answer_tasks(frames, question, scope)
            return ChatResult("report", "", answer.text, report=answer.data)
        if not (REPORT_WORDS.search(question) or PERIOD_WORDS.search(question)):
            return ChatResult("report", "", CAPABILITIES)  # say what is possible instead of an unrelated report
        period = parse_period(question)
        llm = self.llm if self.llm_ready() else None
        if can(user.role, Permission.FINANCE):
            report = build_report(frames, period, llm=llm)
            d = report.data
            text = (f"{report.summary}\n\nIncome {format_eur(d['income'])} | Expenses "
                    f"{format_eur(d['expenses'])} | Net {format_eur(d['net'])}")
            return ChatResult("report", "", text, report=d)
        if can(user.role, Permission.PROJECT_REPORTS):
            data = project_view(build_report(frames, period).data)
            return ChatResult("report", "", template_project_summary(data), report=data)
        return self._denied(user, "reports")

    def _subscription(self, user: User, question: str, frames: Frames) -> ChatResult:
        if not can(user.role, Permission.SUBSCRIPTIONS):
            return self._denied(user, "subscription and billing data")
        analysis = analyze_subscriptions(frames, self.cfg)
        if lookup_kind(question, frames) == "tool_facts":
            answer = answer_tool_facts(frames, self.cfg, question, analysis.flags)
            return ChatResult("subscription", "", answer.text, report=answer.data)
        topic = topic_of(question)
        recs = [r for r in analysis.recommendations if topic is None or r.flag_type == topic]
        by_value = sorted(recs, key=lambda r: -(r.monthly_saving + r.one_off_recovery))
        lines = [f"- {r.text}" for r in by_value[:MAX_LISTED_RECOMMENDATIONS]]
        more = len(by_value) - len(lines)
        if topic is None:
            head = (f"I found {len(analysis.flags)} issues. Estimated savings: "
                    f"{format_eur(analysis.monthly_savings)}/month ({format_eur(analysis.yearly_savings)}/year) "
                    f"plus {format_eur(analysis.one_off_recovery)} in refunds.")
        else:
            head = (f"{len(recs)} {TOPIC_LABELS[topic]} found. Estimated saving: "
                    f"{format_eur(sum(r.monthly_saving for r in recs))}/month"
                    + (f" plus {format_eur(sum(r.one_off_recovery for r in recs))} in refunds."
                       if any(r.one_off_recovery for r in recs) else "."))
        text = (head + "\n\n" + "\n".join(lines) + (f"\n(+{more} more on the Dashboard)" if more > 0 else "")
                + "\n\nThese are recommendations only; nothing is changed without your confirmation.")
        return ChatResult("subscription", "", text, analysis=analysis_to_dict(analysis))


def analysis_to_dict(analysis) -> dict[str, Any]:  # noqa: ANN001 - SubscriptionAnalysis
    """JSON-friendly view of a :class:`SubscriptionAnalysis`."""
    return {
        "monthly_savings": analysis.monthly_savings, "yearly_savings": analysis.yearly_savings,
        "one_off_recovery": analysis.one_off_recovery, "savings_by_tool": analysis.savings_by_tool,
        "recommendations": [{"type": r.flag_type, "tool": r.tool, "month": r.month, "action": r.action,
                             "text": r.text, "monthly_saving": r.monthly_saving,
                             "one_off_recovery": r.one_off_recovery,
                             "requires_confirmation": r.requires_confirmation}
                            for r in analysis.recommendations],
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
