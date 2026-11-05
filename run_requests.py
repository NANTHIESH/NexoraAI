"""Run the five Session 1 requests through the LangGraph agent and save the real output."""
import json
import time
from pathlib import Path

from agent_langgraph import run

REQUESTS = [
    ("Workflow", "What is our company's annual leave policy?"),
    ("Workflow", "How much money did we spend this month?"),
    ("Agent", "Find unused subscriptions, analyse their costs and recommend which ones we should cancel."),
    ("Agent", "Prepare a monthly business performance report using our financial data and project updates."),
    ("Agent", "Check our project updates, identify delayed tasks and suggest what we should do next."),
]
OUT = Path("session works")

lines = ["# Session 5: LangGraph agent on the 5 requests", "",
         "Model: llama3.2:3b via Ollama (local). Graph: planner -> executor -> writer. All tools are read-only.", ""]
raw = []
for i, (kind, request) in enumerate(REQUESTS, 1):
    started = time.perf_counter()
    out = run(request)
    seconds = time.perf_counter() - started
    plan = " -> ".join(f"{s['tool']}({json.dumps(s['args'])})" for s in out["plan"])
    lines += [f"## {i}. {request}", "", f"- Type from Session 1: **{kind}**",
              f"- Plan ({out['plan_source']}): `{plan}`",
              f"- Answer source: {out['answer_source']} | time: {seconds:.1f}s",
              f"- Trace: {'; '.join(out['trace'][1:])}", "", "**Answer:**", "", out["answer"], ""]
    raw.append({"request": request, "seconds": round(seconds, 1), **{k: out[k] for k in
               ("plan", "plan_source", "answer", "answer_source", "trace")}})
    print(f"[{i}/5] {seconds:.1f}s plan={out['plan_source']} answer={out['answer_source']}", flush=True)
(OUT / "session-5-results.md").write_text("\n".join(lines), encoding="utf-8")
(OUT / "session-5-results.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
