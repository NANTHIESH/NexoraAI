"""Chat orchestration: route the question, check permissions, run the right agent, log it."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexora.agents.knowledge import KnowledgeAgent
from nexora.agents.report import build_report, project_view, template_project_summary
from nexora.agents.router import route
from nexora.agents.subscription import analyze_subscriptions
from nexora.agents.tools import format_eur, parse_period
from nexora.config import Config, load_config
from nexora.db.repositories import frames_from_db
from nexora.llm.ollama_client import OllamaClient
from nexora.rag.generator import Assistant
from nexora.security import audit
from nexora.security.auth import User
from nexora.security.pii import redact
from nexora.security.rbac import Permission, can

SMALLTALK_REPLY = ("Hello! I can answer questions about Nexora's company rules (with sources), create "
                   "weekly, monthly or yearly reports, and look for waste in software subscriptions.")
MAX_LISTED_RECOMMENDATIONS = 8


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
        chosen = route(question, self.llm if ready else None)
        result = self._dispatch(conn, user, question, chosen.intent, include_superseded)
        result.intent, result.method = chosen.intent, chosen.method
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
                        detail=f"method={chosen.method} mode={result.mode}")
        return result

    # ------------------------------------------------------------------ intents
    def _dispatch(self, conn: sqlite3.Connection, user: User, question: str, intent: str,
                  include_superseded: bool | None) -> ChatResult:
        if intent == "smalltalk":
            return ChatResult(intent, "", SMALLTALK_REPLY)
        if intent == "knowledge":
            return self._knowledge(question, include_superseded)
        if intent == "report":
            return self._report(conn, user, question)
        return self._subscription(conn, user)

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

    def _report(self, conn: sqlite3.Connection, user: User, question: str) -> ChatResult:
        period = parse_period(question)
        frames = frames_from_db(conn)
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

    def _subscription(self, conn: sqlite3.Connection, user: User) -> ChatResult:
        if not can(user.role, Permission.SUBSCRIPTIONS):
            return self._denied(user, "subscription and billing data")
        analysis = analyze_subscriptions(frames_from_db(conn), self.cfg)
        by_value = sorted(analysis.recommendations, key=lambda r: -(r.monthly_saving + r.one_off_recovery))
        lines = [f"- {r.text}" for r in by_value[:MAX_LISTED_RECOMMENDATIONS]]
        more = len(by_value) - len(lines)
        text = (f"I found {len(analysis.flags)} issues. Estimated savings: "
                f"{format_eur(analysis.monthly_savings)}/month ({format_eur(analysis.yearly_savings)}/year) "
                f"plus {format_eur(analysis.one_off_recovery)} in refunds.\n\n" + "\n".join(lines)
                + (f"\n(+{more} more on the Dashboard)" if more > 0 else "")
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
