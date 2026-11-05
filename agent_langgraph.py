"""Nexora agent rebuilt in LangGraph (Session 5): plan-and-execute with local tools and a local model.

    planner  ->  executor  ->  writer  ->  END

* planner : the local model (Ollama) writes a short list of tool calls as JSON; the plan is validated
            against the tool list, and a safe default plan is used if the model's plan is unusable.
* executor: runs each planned tool call in order. Tools are READ-ONLY and compute every number in Python.
* writer  : the model words the final answer from the tool results only; a number check rejects any
            figure that is not in the results and falls back to a plain summary of the results.

Nothing is written or cancelled. Recommendations that would change data (e.g. cancelling a subscription)
are only suggested, and the answer says a person must approve them.

Run all five assignment requests:  python run_requests.py
"""

from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from typing import Any, TypedDict

import pandas as pd
from langgraph.graph import END, START, StateGraph

from nexora.agents.report import compute_report
from nexora.agents.subscription import analyze_subscriptions
from nexora.config import load_config
from nexora.db.repositories import frames_from_csv
from nexora.llm.ollama_client import OllamaClient
from nexora.rag.generator import Assistant

MAX_STEPS = 4
TASK_LIST_LIMIT = 10
PERIODS = ("weekly", "monthly", "yearly")


# --------------------------------------------------------------------------- tools (all read-only)
@lru_cache(maxsize=1)
def _cfg():
    return load_config()


@lru_cache(maxsize=1)
def _frames():
    return frames_from_csv(_cfg().path("generated"))


@lru_cache(maxsize=1)
def _assistant() -> Assistant:
    return Assistant(_cfg())


def search_company_knowledge(query: str) -> dict:
    """Answer a question about company policy from the company documents, with citations."""
    answer = _assistant().ask(query)
    return {"answer": answer.text, "refused": answer.refused, "citations": answer.citations}


def get_subscription_usage(time_period: str = "monthly", subscription_name: str | None = None,
                           unused_only: bool = False) -> dict:
    """Subscription costs, seats and latest usage; flags unused ones; estimates savings."""
    months = {"monthly": 1, "yearly": 12}.get(time_period)
    if months is None:
        return {"error": "time_period must be 'monthly' or 'yearly'"}
    frames, cfg = _frames(), _cfg()
    analysis = analyze_subscriptions(frames, cfg)
    unused = {f.tool for f in analysis.flags if f.type == "unused_subscription"}
    latest = frames.usage_log.sort_values("month").groupby("tool").tail(1).set_index("tool")
    rows = []
    for sub in frames.subscriptions.itertuples():
        if subscription_name and sub.tool.lower() != str(subscription_name).lower():
            continue
        if unused_only and sub.tool not in unused:
            continue
        use = latest.loc[sub.tool] if sub.tool in latest.index else None
        rows.append({"subscription": sub.tool, "cost_eur": round(float(sub.monthly_cost) * months, 2),
                     "seats": int(sub.seats),
                     "active_users_latest_month": int(use["active_users"]) if use is not None else None,
                     "flagged_unused": sub.tool in unused})
    return {"time_period": time_period, "count": len(rows),
            "total_cost_eur": round(sum(r["cost_eur"] for r in rows), 2), "subscriptions": rows,
            "estimated_monthly_saving_if_unused_cancelled_eur": round(
                sum(r["cost_eur"] for r in rows if r["flagged_unused"]) / months, 2)}


def get_financial_summary(period: str = "monthly") -> dict:
    """Income, expenses, net result and top spending categories for the latest week, month or year of data."""
    if period not in PERIODS:
        return {"error": f"period must be one of {PERIODS}"}
    data = compute_report(_frames(), period)
    top = dict(list(data["expenses_by_category"].items())[:5])
    return {"period": period, "from": data["start"], "to": data["end"], "income_eur": data["income"],
            "expenses_eur": data["expenses"], "net_eur": data["net"], "top_expense_categories_eur": top,
            "biggest_cost_increases": [
                {"category": i["category"], "this_period_eur": i["current"], "previous_period_eur": i["previous"],
                 "increase_eur": i["increase"]} for i in data["top_cost_increases"]],
            "tasks_completed_in_period": data["tasks"]["done"], "tasks_blocked_now": data["tasks"]["blocked"],
            "tasks_overdue_now": data["tasks"]["overdue"]}


def get_project_tasks(status: str = "overdue", project: str | None = None) -> dict:
    """List project tasks by status: overdue, blocked, done, in_progress, todo or all."""
    frames = _frames()
    tasks = frames.tasks.copy()
    today = pd.Period(frames.transactions["month"].max(), "M").end_time.normalize()  # latest data month end
    if project:
        tasks = tasks[tasks["project"].str.lower() == str(project).lower()]
    if status == "overdue":
        chosen = tasks[(tasks["status"] != "done") & (tasks["due"] < today)]
    elif status in ("blocked", "done", "in_progress", "todo"):
        chosen = tasks[tasks["status"] == status]
    elif status == "all":
        chosen = tasks
    else:
        return {"error": "status must be overdue, blocked, done, in_progress, todo or all"}
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    chosen = chosen.assign(_p=chosen["priority"].map(order)).sort_values(["_p", "due"])
    items = [{"task_id": r.task_id, "project": r.project, "title": r.title, "owner": r.owner,
              "status": r.status, "priority": r.priority, "due": r.due.date().isoformat(),
              "days_late": int((today - r.due).days) if r.status != "done" and r.due < today else 0,
              "note": r.weekly_note} for r in chosen.head(TASK_LIST_LIMIT).itertuples()]
    return {"status": status, "as_of": today.date().isoformat(), "count": len(chosen),
            "count_by_project": chosen["project"].value_counts().to_dict(),
            "count_by_task_status": chosen["status"].value_counts().to_dict(),
            "listed_tasks_sorted_by": "priority, then earliest due date (not by days late)",
            "tasks": items}


TOOLS: dict[str, dict[str, Any]] = {
    "search_company_knowledge": {
        "fn": search_company_knowledge, "access": "read",
        "doc": "Company policies and rules from documents (leave, remote work, expenses...). Args: query (str).",
        "args": {"query": None}},
    "get_subscription_usage": {
        "fn": get_subscription_usage, "access": "read",
        "doc": "Software subscriptions: cost, seats, usage, which are unused, savings if unused ones are cancelled. "
               "Args: time_period ('monthly'|'yearly'), subscription_name (optional), unused_only (bool).",
        "args": {"time_period": {"monthly", "yearly"}, "subscription_name": None, "unused_only": None}},
    "get_financial_summary": {
        "fn": get_financial_summary, "access": "read",
        "doc": "Money: income, total spending/expenses, net result, top spending categories. "
               "Args: period ('weekly'|'monthly'|'yearly').",
        "args": {"period": set(PERIODS)}},
    "get_project_tasks": {
        "fn": get_project_tasks, "access": "read",
        "doc": "Project tasks and updates. Args: status ('overdue'|'blocked'|'done'|'in_progress'|'todo'|'all'), "
               "project (optional name).",
        "args": {"status": {"overdue", "blocked", "done", "in_progress", "todo", "all"}, "project": None}},
}


# --------------------------------------------------------------------------- graph
class AgentState(TypedDict, total=False):
    request: str
    plan: list[dict]
    plan_source: str          # "llm" or "default" (model plan unusable)
    results: list[dict]
    answer: str
    answer_source: str        # "llm", or "results" when the number check rejected the model's wording
    trace: list[str]


_llm = OllamaClient()

PLANNER_PROMPT = """You are the planner of a business assistant. Choose which tools to call to answer the request.
Use as few steps as needed (at most 4) and only these tools:
{catalog}

Reply with JSON only: {{"steps": [{{"tool": "<tool name>", "args": {{...}}}}]}}
Examples:
Request: How many vacation days do I get?
{{"steps": [{{"tool": "search_company_knowledge", "args": {{"query": "How many vacation days do I get?"}}}}]}}
Request: Which software tools are unused and what would we save?
{{"steps": [{{"tool": "get_subscription_usage", "args": {{"time_period": "monthly", "unused_only": true}}}}]}}"""

WRITER_PROMPT = """You write the final answer for a business user from tool results.
Rules: use ONLY facts and numbers that appear in the results; never invent or calculate new numbers;
if the results do not contain the answer, say so. Be concise (under 150 words). Copy labels exactly: never
attach a number to a different label. Only if you recommend cancelling a subscription, say a person must
approve it first because nothing has been changed; otherwise do not mention approvals."""


def _clean_step(step: Any) -> dict | None:
    """Keep a planned step only if the tool exists; drop unknown or invalid arguments."""
    if not isinstance(step, dict) or step.get("tool") not in TOOLS:
        return None
    spec, raw = TOOLS[step["tool"]]["args"], step.get("args") or {}
    args = {}
    for key, value in (raw.items() if isinstance(raw, dict) else []):
        if key in spec and (spec[key] is None or value in spec[key]) and value is not None:
            args[key] = value
    if step["tool"] == "search_company_knowledge" and "query" not in args:
        return None
    return {"tool": step["tool"], "args": args}


def default_plan(request: str) -> list[dict]:
    """Used only when the model's plan is unusable: answer from the documents."""
    return [{"tool": "search_company_knowledge", "args": {"query": request}}]


def planner(state: AgentState) -> AgentState:
    catalog = "\n".join(f"- {name}: {t['doc']}" for name, t in TOOLS.items())
    plan: list[dict] = []
    try:
        raw = json.loads(_llm.chat(PLANNER_PROMPT.format(catalog=catalog), f"Request: {state['request']}",
                                   json_mode=True))
        plan = [s for s in (_clean_step(x) for x in raw.get("steps", [])[:MAX_STEPS]) if s]
    except Exception:  # noqa: BLE001 - bad JSON, Ollama error...
        plan = []
    source = "llm" if plan else "default"
    plan = plan or default_plan(state["request"])
    for step in plan:  # a rewritten question can miss the document; always search with the user's own words
        if step["tool"] == "search_company_knowledge":
            step["args"]["query"] = state["request"]
    return {"plan": plan, "plan_source": source, "trace": [f"plan ({source}): {json.dumps(plan)}"]}


def executor(state: AgentState) -> AgentState:
    results, trace = [], list(state.get("trace", []))
    for step in state["plan"]:
        started = time.perf_counter()
        try:
            output = TOOLS[step["tool"]]["fn"](**step["args"])
        except Exception as exc:  # noqa: BLE001 - a failing tool must not crash the run
            output = {"error": f"{type(exc).__name__}: {exc}"}
        results.append({"tool": step["tool"], "args": step["args"], "output": output})
        trace.append(f"ran {step['tool']} in {time.perf_counter() - started:.1f}s")
    return {"results": results, "trace": trace}


_NUMBER = re.compile(r"\d[\d,]*\.?\d*")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".").rstrip("0").rstrip(".") if "." in n else n.replace(",", "").rstrip(".")
            for n in _NUMBER.findall(text)}


def _plain_summary(results: list[dict]) -> str:
    parts = []
    for r in results:
        out = r["output"]
        parts.append(f"[{r['tool']}] " + (out["answer"] if isinstance(out, dict) and "answer" in out
                                          else json.dumps(out, default=str)[:1200]))
    return "\n".join(parts)


def writer(state: AgentState) -> AgentState:
    results, trace = state["results"], list(state.get("trace", []))
    facts = json.dumps(results, default=str)
    try:
        text = _llm.chat(WRITER_PROMPT, f"Request: {state['request']}\n\nTool results:\n{facts}")
    except Exception as exc:  # noqa: BLE001
        trace.append(f"writer unavailable ({type(exc).__name__}); showing results")
        return {"answer": _plain_summary(results), "answer_source": "results", "trace": trace}
    known = _numbers(facts) | _numbers(state["request"])
    invented = sorted(n for n in _numbers(text) if n not in known and len(n) > 1)
    if invented:  # reflection step: reject wording that contains figures not in the tool results
        trace.append(f"number check FAILED, figures not in results: {invented}; showing results instead")
        return {"answer": _plain_summary(results), "answer_source": "results", "trace": trace}
    trace.append("number check passed")
    return {"answer": text, "answer_source": "llm", "trace": trace}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("planner", planner)
    graph.add_node("executor", executor)
    graph.add_node("writer", writer)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "writer")
    graph.add_edge("writer", END)
    return graph.compile()


def run(request: str) -> AgentState:
    return build_graph().invoke({"request": request})


if __name__ == "__main__":
    import sys

    out = run(" ".join(sys.argv[1:]) or "Which subscriptions are unused?")
    print(json.dumps({k: out[k] for k in ("plan", "plan_source", "answer", "answer_source", "trace")},
                     indent=2, default=str))
