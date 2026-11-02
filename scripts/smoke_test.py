"""End-to-end smoke test against a RUNNING API: a chat answer, a refusal, a report, an analysis.

Run: python -m scripts.smoke_test [--url http://localhost:8000]
Needs `make data load index` done and the API started with `make api`. Exit code 1 on failure.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from nexora.config import load_config

OPS_MANAGER = "E002"


def main() -> int:
    """Run the four checks and print PASS/FAIL for each."""
    cfg = load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=cfg.api.base_url)
    args = parser.parse_args()
    client = httpx.Client(base_url=args.url, timeout=300)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    try:
        health = client.get("/health").json()
    except httpx.ConnectError:
        print(f"Cannot reach the API at {args.url}. Start it with `make api`.", file=sys.stderr)
        return 1
    print(f"API up; mode={health['mode']} model={health['model']}")
    login = client.post("/login", json={"employee_id": OPS_MANAGER, "password": cfg.auth.demo_password})
    if login.status_code != 200:
        print(f"Login failed ({login.status_code}): {login.text}. Run `make load` first.", file=sys.stderr)
        return 1
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    def chat(question: str) -> dict:
        resp = client.post("/chat", json={"question": question}, headers=headers)
        resp.raise_for_status()
        return resp.json()

    answer = chat("How many vacation days do full-time employees get?")
    check("chat answer", "25" in answer["text"] and bool(answer["sources"]), answer["text"][:90])
    refusal = chat("What is the annual bonus structure?")
    check("refusal", refusal["refused"] and refusal["text"] == cfg.refusal_text, refusal["text"][:90])
    report = client.get("/report?period=monthly", headers=headers).json()
    check("monthly report", report["report"]["income"] > 0 and bool(report["summary"]),
          f"income {report['report']['income']:,.0f} EUR, summary by {report['summary_source']}")
    analysis = client.get("/subscriptions/analysis", headers=headers).json()
    check("subscription analysis", len(analysis["flags"]) > 0 and analysis["monthly_savings"] > 0,
          f"{len(analysis['flags'])} flags, {analysis['monthly_savings']:,.0f} EUR/month")
    failed = [name for name, ok, _ in results if not ok]
    print("\nSmoke test:", "FAILED " + ", ".join(failed) if failed else "all checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
