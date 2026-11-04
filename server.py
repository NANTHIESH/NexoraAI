"""Nexora MCP server (Session 3).

Exposes one READ-ONLY tool, ``get_subscription_usage``, over the Model Context Protocol (stdio).
It reads the generated CSV files and reuses the existing subscription detectors; it changes nothing.

Run:   python server.py            (an MCP client starts it; it waits for requests on stdin)
Check: npx @modelcontextprotocol/inspector python server.py

With stdio transport, stdout carries the protocol, so never print() to stdout here.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from nexora.agents.subscription import analyze_subscriptions
from nexora.config import load_config
from nexora.db.repositories import frames_from_csv

logging.basicConfig(stream=sys.stderr, level=logging.INFO)

mcp = FastMCP("nexora")

PERIOD_MONTHS = {"monthly": 1, "yearly": 12}


@mcp.tool()
def get_subscription_usage(time_period: str, subscription_name: str | None = None,
                           unused_only: bool = False) -> dict:
    """Retrieve software subscription costs, seats and usage, and flag unused ones.

    Read-only. Use it for questions about what a tool costs, how many people use it,
    or which subscriptions look unused. Do not use it to cancel anything.

    Args:
        time_period: "monthly" or "yearly". The cost is reported for that period.
        subscription_name: Optional tool name, e.g. "Slack". Leave empty for all subscriptions.
        unused_only: If true, return only subscriptions the detectors flag as unused.
    """
    if time_period not in PERIOD_MONTHS:
        return {"error": f"time_period must be one of {sorted(PERIOD_MONTHS)}, got {time_period!r}"}

    cfg = load_config()
    frames = frames_from_csv(cfg.path("generated"))
    analysis = analyze_subscriptions(frames, cfg)
    unused = {f.tool for f in analysis.flags if f.type == "unused_subscription"}

    latest = frames.usage_log.sort_values("month").groupby("tool").tail(1).set_index("tool")
    months = PERIOD_MONTHS[time_period]
    rows = []
    for sub in frames.subscriptions.itertuples():
        if subscription_name and sub.tool.lower() != subscription_name.lower():
            continue
        if unused_only and sub.tool not in unused:
            continue
        usage = latest.loc[sub.tool] if sub.tool in latest.index else None
        rows.append({
            "subscription": sub.tool,
            "plan": sub.plan,
            "cost_eur": round(float(sub.monthly_cost) * months, 2),
            "seats": int(sub.seats),
            "active_users_latest_month": int(usage["active_users"]) if usage is not None else None,
            "latest_usage_month": str(usage["month"]) if usage is not None else None,
            "flagged_unused": sub.tool in unused,
        })

    if subscription_name and not rows:
        return {"error": f"No subscription named {subscription_name!r}",
                "known": sorted(frames.subscriptions["tool"])}
    return {
        "time_period": time_period,
        "count": len(rows),
        "total_cost_eur": round(sum(r["cost_eur"] for r in rows), 2),
        "subscriptions": rows,
    }


if __name__ == "__main__":
    mcp.run()  # stdio transport
