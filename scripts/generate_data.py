"""Generate ALL synthetic data for the Nexora demo company, deterministically.

Run: python -m scripts.generate_data [--seed 42] [--out data/generated]   (or: make data)

Outputs: employees.csv, subscriptions.csv, usage_log.csv, transactions.csv, tasks.csv,
plan_changes.csv and ground_truth.json (20 planted errors + decoys). Same seed gives
byte-identical files. Money is handled in integer cents to avoid float drift.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from faker import Faker

from nexora.config import load_config

# --------------------------------------------------------------------------------------
# Demo-company specification (data, not configuration)
# --------------------------------------------------------------------------------------

CLIENTS = {
    "Brightwave Retail": {"retainer": 3_400_000, "project": "ShopFlow Web"},
    "Pulse Fitness": {"retainer": 2_700_000, "project": "FitTrack Mobile"},
    "Northgate Logistics": {"retainer": 3_100_000, "project": "LogiDash"},
}
PROJECTS = ("ShopFlow Web", "FitTrack Mobile", "LogiDash", "Internal Tools")

# (role, job_title, department, gross monthly salary in cents)
STAFF = (
    ("owner", "CEO & Owner", "Management", 900_000),
    ("ops_manager", "Office & Operations Manager", "Operations", 520_000),
    ("pm", "Project Manager", "Delivery", 620_000),
    ("employee", "Senior Developer", "Engineering", 680_000),
    *[("employee", "Developer", "Engineering", 550_000)] * 6,
    *[("employee", "QA Engineer", "Quality", 440_000)] * 2,
    ("employee", "UI/UX Designer", "Design", 480_000),
    ("employee", "DevOps Engineer", "Engineering", 620_000),
    ("employee", "Sales Manager", "Sales", 560_000),
)


@dataclass(frozen=True)
class Tool:
    """One software subscription of the demo company."""

    name: str
    group: str
    plan: str
    pricing: str  # fixed | usage_based | annual
    seats: int
    cost_cents: int  # monthly cost (annual tools: monthly equivalent)
    billing_day: int
    owner_title: str
    descriptor: str  # bank-statement style description


TOOLS: tuple[Tool, ...] = (
    Tool("GitHub", "dev", "Team", "fixed", 15, 6_000, 3, "Senior Developer", "GITHUB *TEAM 15 SEATS"),
    Tool("JetBrains", "dev", "All Products Pack", "annual", 7, 20_230, 12, "Senior Developer", "JETBRAINS *ALL PRODUCTS ANNUAL"),
    Tool("Docker", "dev", "Business", "fixed", 7, 16_800, 5, "DevOps Engineer", "DOCKER INC *BUSINESS 7 SEATS"),
    Tool("Postman", "dev", "Team", "fixed", 7, 9_800, 9, "Senior Developer", "POSTMAN *TEAM 7 SEATS"),
    Tool("Sentry", "dev", "Team", "fixed", 7, 2_600, 14, "DevOps Engineer", "SENTRY.IO *TEAM"),
    Tool("AWS", "cloud", "Pay-as-you-go", "usage_based", 7, 145_000, 2, "DevOps Engineer", "AWS EMEA *SERVICES"),
    Tool("Cloudflare", "cloud", "Pro", "fixed", 2, 5_000, 18, "DevOps Engineer", "CLOUDFLARE *PRO 2 DOMAINS"),
    Tool("Datadog", "cloud", "Pro", "fixed", 5, 12_000, 20, "DevOps Engineer", "DATADOG *PRO 5 HOSTS"),
    Tool("Slack", "collaboration", "Pro", "fixed", 15, 10_875, 1, "Office & Operations Manager", "SLACK *PRO 15 SEATS"),
    Tool("Zoom", "collaboration", "Pro", "fixed", 7, 9_793, 7, "Office & Operations Manager", "ZOOM.US *PRO 7 LICENSES"),
    Tool("Google Workspace", "collaboration", "Business Standard", "fixed", 15, 20_400, 2, "Office & Operations Manager", "GOOGLE *WORKSPACE 15 USERS"),
    Tool("Notion", "collaboration", "Plus", "fixed", 15, 15_000, 16, "Office & Operations Manager", "NOTION LABS *PLUS 15 SEATS"),
    Tool("Confluence", "collaboration", "Standard", "fixed", 15, 8_625, 22, "Project Manager", "ATLASSIAN *CONFLUENCE STD"),
    Tool("Jira", "planning_design", "Standard", "fixed", 15, 11_625, 11, "Project Manager", "ATLASSIAN *JIRA STD"),
    Tool("Linear", "planning_design", "Standard", "fixed", 8, 6_400, 6, "Project Manager", "LINEAR.APP *STANDARD 8 SEATS"),
    Tool("Figma", "planning_design", "Professional", "fixed", 3, 4_500, 10, "UI/UX Designer", "FIGMA *PROFESSIONAL 3 SEATS"),
    Tool("1Password", "security", "Business", "fixed", 15, 11_985, 15, "Office & Operations Manager", "1PASSWORD *BUSINESS 15 USERS"),
    Tool("Microsoft Teams", "collaboration", "Teams Essentials", "fixed", 15, 6_000, 24, "Office & Operations Manager", "MICROSOFT *TEAMS ESSENTIALS"),
)
TOOL_BY_NAME = {t.name: t for t in TOOLS}

# (name, descriptor, monthly cost cents, day of month): recurring charges NOT in subscriptions.
UNLISTED = (
    ("Canva", "CANVA* PRO TEAM", 5_200, 8),
    ("Dropbox", "DROPBOX *BUSINESS", 15_000, 13),
    ("Loom", "LOOM *BUSINESS 5 CREATORS", 8_000, 19),
)

DUPLICATE_TOOLS = ("GitHub", "Docker", "Cloudflare", "Google Workspace", "Sentry")
PRICE_TOOLS = ("1Password", "Figma", "Postman", "Docker", "Cloudflare")
UNUSED_TOOLS = ("Datadog", "Zoom", "Postman", "Sentry")
OVERLAP_PAIRS = (("Jira", "Linear"), ("Notion", "Confluence"), ("Slack", "Microsoft Teams"))

COMPONENTS = {
    "ShopFlow Web": ("checkout", "product catalog", "cart", "search", "admin panel", "order API"),
    "FitTrack Mobile": ("workout tracker", "push notifications", "onboarding", "sync service", "profile"),
    "LogiDash": ("route map", "shipment table", "driver app", "reports", "alerts engine"),
    "Internal Tools": ("CI pipeline", "time tracking", "invoice export", "deploy script", "wiki"),
}
BUG_SYMPTOMS = ("crash", "wrong total", "slow response", "layout glitch", "timeout", "missing validation")
STORY_GOALS = ("filter by date", "export to CSV", "dark mode", "bulk edit", "email digest", "audit trail")
CHORES = ("upgrade dependencies", "write tests", "refactor", "document", "set up monitoring", "clean up")
NOTES = {
    "blocked": ("Blocked: waiting on client sign-off", "Blocked: needs API access", "Blocked: dependency on another team"),
    "in_progress": ("On track", "Needs review this week", "Slightly behind estimate"),
    "done": ("Shipped in sprint review", "Merged and deployed", ""),
    "todo": ("", "", "Planned for next sprint"),
}


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def fmt_cents(cents: int) -> str:
    """Format integer cents as a two-decimal string."""
    return f"{cents / 100:.2f}"


def month_starts(start: str, count: int) -> list[date]:
    """First day of ``count`` consecutive months beginning at ``YYYY-MM``."""
    year, month = (int(p) for p in start.split("-"))
    out = []
    for _ in range(count):
        out.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def label(d: date) -> str:
    """``YYYY-MM`` label of a date."""
    return f"{d:%Y-%m}"


def last_day(d: date) -> date:
    """Last calendar day of the month containing ``d``."""
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def sub_rng(seed: int, name: str) -> random.Random:
    """Independent, deterministic RNG per generator section."""
    return random.Random(f"{seed}:{name}")


def write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    """Write a CSV with LF line endings (byte-stable across platforms)."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


# --------------------------------------------------------------------------------------
# Employees
# --------------------------------------------------------------------------------------


def make_employees(seed: int, first_window_day: date) -> list[dict[str, Any]]:
    """15 employees with deterministic names, roles and hire dates."""
    rng = sub_rng(seed, "employees")
    fake = Faker("en_GB")
    fake.seed_instance(seed)
    employees, used_emails = [], set()
    for i, (role, title, dept, gross) in enumerate(STAFF, start=1):
        first, last = fake.first_name(), fake.last_name()
        slug = re.sub(r"[^a-z]", "", f"{first}.{last}".lower().replace(" ", "")) or f"user{i}"
        local = re.sub(r"[^a-z.]", "", f"{first}.{last}".lower())
        email = f"{local}@nexora.example"
        if email in used_emails:
            email = f"{slug}{i}@nexora.example"
        used_emails.add(email)
        hired = date(2019, 1, 1) + timedelta(days=rng.randrange(0, 2300))
        assert hired < first_window_day
        employees.append(
            {
                "employee_id": f"E{i:03d}",
                "name": f"{first} {last}",
                "email": email,
                "role": role,
                "job_title": title,
                "department": dept,
                "hire_date": hired.isoformat(),
                "gross_monthly_salary": gross + rng.randrange(-5, 6) * 1000,
            }
        )
    return employees


# --------------------------------------------------------------------------------------
# Error planning
# --------------------------------------------------------------------------------------


class Planner:
    """Chooses months for planted errors/decoys; keeps same-tool events apart."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.used: dict[str, set[int]] = {}

    def pick(self, tool: str, lo: int, hi: int, width: int = 1) -> int:
        """Pick a start month in [lo, hi] at least 2 months from earlier events on ``tool``."""
        taken = self.used.setdefault(tool, set())
        options = [
            m for m in range(lo, hi + 1)
            if all(abs(m + k - u) >= 2 for u in taken for k in range(width))
        ]
        month = self.rng.choice(options)
        taken.update(month + k for k in range(width))
        return month


def plan_errors(seed: int, months: int) -> dict[str, Any]:
    """Randomly (but deterministically) place every planted error and decoy."""
    assert months == 12, "error windows are defined for a 12-month dataset"
    pl = Planner(sub_rng(seed, "errors"))
    rng = pl.rng
    plan: dict[str, Any] = {}
    plan["duplicates"] = {t: pl.pick(t, 1, 10) for t in DUPLICATE_TOOLS}
    plan["price"] = {t: (pl.pick(t, 2, 9), round(rng.uniform(0.15, 0.60), 2)) for t in PRICE_TOOLS}
    plan["unused"] = {t: pl.pick(t, 4, 8) for t in UNUSED_TOOLS}
    plan["unlisted"] = {name: rng.randint(1, 6) for name, *_ in UNLISTED}
    plan["overlap"] = {pair[1]: rng.randint(2, 8) for pair in OVERLAP_PAIRS}
    plan["aws_spike"] = rng.randint(6, 10)
    plan["jetbrains_renewal"] = rng.randint(0, 11)
    plan["zoom_change"] = (pl.pick("Zoom", 2, 5), round(rng.uniform(0.30, 0.45), 2))
    plan["figma_gap"] = pl.pick("Figma", 2, 8, width=2)
    plan["slack_prorated"] = rng.randint(3, 10)
    return plan


# --------------------------------------------------------------------------------------
# Subscriptions, usage, transactions
# --------------------------------------------------------------------------------------


def tool_start_index(tool: Tool, plan: dict[str, Any]) -> int:
    """Index of the first month in which ``tool`` is billed."""
    return plan["overlap"].get(tool.name, 0)


def current_cost(tool: Tool, idx: int, plan: dict[str, Any]) -> int:
    """Monthly price of a fixed-price tool in month ``idx`` after price events."""
    cost = tool.cost_cents
    if tool.name in plan["price"]:
        month, pct = plan["price"][tool.name]
        if idx >= month:
            cost = round(cost * (1 + pct))
    if tool.name == "Zoom":
        month, pct = plan["zoom_change"]
        if idx >= month:
            cost = round(cost * (1 + pct))
    return cost


def make_usage(seed: int, months: list[date], plan: dict[str, Any]) -> list[list[Any]]:
    """usage_log rows: tool, month, active_users, logins, last_used."""
    rng = sub_rng(seed, "usage")
    rows = []
    for tool in TOOLS:
        last_active: date | None = None
        for idx in range(tool_start_index(tool, plan), len(months)):
            first = months[idx]
            gap = plan["figma_gap"]
            idle = (
                (tool.name in plan["unused"] and idx >= plan["unused"][tool.name])
                or (tool.name == "Figma" and gap <= idx < gap + 2)
            )
            if idle:
                active, logins = 0, 0
                stamp = last_active or first - timedelta(days=1)
            else:
                active = max(1, round(tool.seats * rng.uniform(0.6, 1.0)))
                logins = active * rng.randint(12, 28)
                stamp = first + timedelta(days=rng.randint(max(0, last_day(first).day - 7), last_day(first).day - 1))
                last_active = last_day(first)
            rows.append([tool.name, label(first), active, logins, stamp.isoformat()])
    return rows


class TxnBook:
    """Collects transactions with tags so ground truth can reference final txn ids."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, when: date, desc: str, cat: str, who: str, cents: int, *tags: str) -> None:
        """Append a transaction (negative cents = money out)."""
        self.items.append(
            {"date": when, "desc": desc, "cat": cat, "who": who, "cents": cents,
             "tags": list(tags), "seq": len(self.items)}
        )


def add_software(book: TxnBook, seed: int, months: list[date], plan: dict[str, Any]) -> None:
    """One charge per subscription per month, plus planted duplicates/unlisted/decoys."""
    rng = sub_rng(seed, "software")
    for tool in TOOLS:
        cat = "Cloud" if tool.name == "AWS" else "Software"
        for idx in range(tool_start_index(tool, plan), len(months)):
            when = months[idx].replace(day=tool.billing_day)
            tags = [f"sw|{tool.name}|{idx}"]
            if tool.pricing == "annual":
                if idx != plan["jetbrains_renewal"]:
                    continue
                cents, tags = tool.cost_cents * 12, tags + ["decoy|annual_renewal"]
            elif tool.pricing == "usage_based":
                cents = round(tool.cost_cents * rng.uniform(0.85, 1.15))
                if idx == plan["aws_spike"]:
                    cents, tags = round(tool.cost_cents * 1.75), tags + ["decoy|aws_spike"]
            else:
                cents = current_cost(tool, idx, plan)
                if tool.name in plan["price"] and idx == plan["price"][tool.name][0]:
                    tags.append(f"price|{tool.name}")
                if tool.name in plan["unused"] and idx >= plan["unused"][tool.name]:
                    tags.append(f"unused|{tool.name}")
            if tool.name == "Zoom" and idx == plan["zoom_change"][0]:
                tags.append("decoy|plan_change")
            if tool.name == "Figma" and idx == plan["figma_gap"]:
                tags.append("decoy|usage_gap")
            if plan["duplicates"].get(tool.name) == idx:
                tags.append(f"dup|{tool.name}")
            book.add(when, tool.descriptor, cat, tool.name, -cents, *tags)
            if f"dup|{tool.name}" in tags:
                extra = cents if rng.random() < 0.5 else round(cents * rng.uniform(0.99, 1.01))
                book.add(when + timedelta(days=rng.randint(1, 5)), tool.descriptor, cat,
                         tool.name, -extra, f"dup|{tool.name}")
            if tool.name == "Slack" and idx == plan["slack_prorated"]:
                book.add(when + timedelta(days=3), "SLACK *PRO SEAT ADD-ON PRORATED", cat,
                         "Slack", -725, "decoy|prorated_seat")
    for name, desc, cents, day in UNLISTED:
        for idx in range(plan["unlisted"][name], len(months)):
            book.add(months[idx].replace(day=day), desc, "Software", name, -cents, f"unlisted|{name}")


def add_income(book: TxnBook, seed: int, months: list[date], end: date) -> None:
    """Client retainers and milestone invoices; some paid 10 to 30 days late."""
    rng = sub_rng(seed, "income")
    invoice_no = 0
    invoices: list[tuple[date, str, int, str]] = []
    for client, info in CLIENTS.items():
        for first in months:
            invoices.append((first, client, info["retainer"], f"{info['project']} retainer"))
        for m in rng.sample(range(0, 10), 4):
            amount = rng.randrange(800_000, 3_000_001, 10_000)
            invoices.append((months[m].replace(day=rng.randint(5, 20)), client, amount,
                             f"{info['project']} milestone"))
    for issued, client, amount, kind in sorted(invoices, key=lambda x: (x[0], x[1])):
        invoice_no += 1
        delay = 30 + rng.randint(10, 30) if rng.random() < 0.2 else rng.randint(7, 30)
        paid = issued + timedelta(days=delay)
        if paid <= end:
            book.add(paid, f"INV-{invoice_no:04d} {kind}", "Income", client, amount)


def add_overheads(book: TxnBook, seed: int, months: list[date], employees: list[dict[str, Any]]) -> None:
    """Payroll, rent, insurance, taxes and the variable cost lines."""
    rng = sub_rng(seed, "overheads")
    gross_total = sum(e["gross_monthly_salary"] for e in employees)
    contractors = ("Studio Kappa", "Lena Frontend Freelance", "Nordic QA Services", "PixelCraft Agency")
    for first in months:
        mlab = first.strftime("%b %Y").upper()
        for emp in employees:
            book.add(first.replace(day=28), f"PAYROLL {emp['employee_id']} {mlab}", "Payroll",
                     emp["employee_id"], -round(emp["gross_monthly_salary"] * 0.68))
        book.add(first.replace(day=28), f"EMPLOYER SOCIAL CONTRIBUTIONS {mlab}", "Payroll Taxes",
                 "Social Insurance Fund", -round(gross_total * 0.20))
        book.add(first, "OFFICE COWORKING RENT", "Rent", "Spaces Coworking", -380_000)
        book.add(first.replace(day=5), "BUSINESS LIABILITY AND CYBER INSURANCE", "Insurance",
                 "Hanse Insurance", -64_000)
        book.add(first.replace(day=9), "INTERNET AND PHONE", "Utilities", "Telekom", -18_000)
        book.add(first.replace(day=15), "ACCOUNTING AND BOOKKEEPING", "Accounting",
                 "Meyer Tax Advisors", -90_000)
        book.add(first.replace(day=30 if first.month != 2 else 28), "ACCOUNT FEES", "Bank Fees",
                 "Northbank", -2_490)
        for _ in range(rng.randint(1, 3)):
            book.add(first.replace(day=rng.randint(3, 25)), "CONTRACTOR INVOICE", "Contractors",
                     rng.choice(contractors), -rng.randrange(180_000, 600_001, 5_000))
        if rng.random() < 0.6:
            book.add(first.replace(day=rng.randint(3, 25)), "HARDWARE PURCHASE", "Hardware",
                     rng.choice(("Dell", "Apple Store", "Amazon Business")),
                     -rng.randrange(90_000, 240_001, 5_000))
        for _ in range(rng.randint(2, 4)):
            book.add(first.replace(day=rng.randint(1, 27)), "TRAVEL", "Travel",
                     rng.choice(("Deutsche Bahn", "Lufthansa", "Booking.com", "Uber")),
                     -rng.randrange(1_500, 45_001, 100))
        for _ in range(rng.randint(12, 16)):
            book.add(first.replace(day=rng.randint(1, 27)), "OFFICE SUPPLIES", "Office",
                     rng.choice(("Amazon Business", "IKEA", "Staples", "Rewe")),
                     -rng.randrange(800, 9_001, 50))
        for _ in range(rng.randint(10, 14)):
            emp = rng.choice(employees)["employee_id"]
            book.add(first.replace(day=rng.randint(1, 27)), f"EXPENSE REIMBURSEMENT {emp}",
                     "Reimbursements", emp, -rng.randrange(2_000, 18_001, 100))
        for _ in range(rng.randint(6, 10)):
            book.add(first.replace(day=rng.randint(1, 27)), "TEAM MEALS", "Meals",
                     rng.choice(("Restaurant Alte Post", "Lieferando", "Cafe Nord")),
                     -rng.randrange(1_500, 22_001, 100))
        if first.month in (1, 4, 7, 10):
            book.add(first.replace(day=10), "VAT PAYMENT QUARTERLY", "Taxes", "Tax Office",
                     -rng.randrange(1_400_000, 1_900_001, 10_000))
            book.add(first.replace(day=10), "CORPORATE TAX ADVANCE", "Taxes", "Tax Office",
                     -rng.randrange(600_000, 900_001, 10_000))
    for m in rng.sample(range(len(months)), 5):
        book.add(months[m].replace(day=rng.randint(5, 20)), "CONFERENCE TICKETS", "Conferences",
                 rng.choice(("PyCon DE", "JSConf EU", "KubeCon EU", "Web Summit")),
                 -rng.randrange(40_000, 180_001, 5_000))
    for m in rng.sample(range(len(months)), 10):
        book.add(months[m].replace(day=rng.randint(5, 20)), "ONLINE TRAINING", "Training",
                 rng.choice(("Udemy Business", "Pluralsight", "Coursera", "O'Reilly")),
                 -rng.randrange(10_000, 100_001, 5_000))


def finalize_transactions(book: TxnBook, opening_cents: int) -> tuple[list[list[Any]], dict[str, list[str]]]:
    """Sort, assign ids, compute balance. Returns csv rows and tag -> txn_ids."""
    ordered = sorted(book.items, key=lambda t: (t["date"], t["seq"]))
    balance, rows, tag_ids = opening_cents, [], {}
    for n, txn in enumerate(ordered, start=1):
        txn_id = f"T{n:04d}"
        balance += txn["cents"]
        rows.append([txn_id, txn["date"].isoformat(), txn["desc"], txn["cat"], txn["who"],
                     fmt_cents(txn["cents"]), fmt_cents(balance)])
        for tag in txn["tags"]:
            tag_ids.setdefault(tag, []).append(txn_id)
    return rows, tag_ids


# --------------------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------------------


def make_tasks(seed: int, employees: list[dict[str, Any]], start: date, end: date, count: int) -> list[list[Any]]:
    """About ``count`` Jira-like issues across the four projects."""
    rng = sub_rng(seed, "tasks")
    workers = [e["employee_id"] for e in employees if e["job_title"] not in
               ("CEO & Owner", "Sales Manager", "Office & Operations Manager", "Project Manager")]
    span = (end - start).days
    rows = []
    for n in range(1, count + 1):
        project = rng.choices(PROJECTS, weights=(35, 25, 25, 15))[0]
        kind = rng.choices(("bug", "story", "task"), weights=(35, 35, 30))[0]
        comp = rng.choice(COMPONENTS[project])
        if kind == "bug":
            title, points = f"Fix {rng.choice(BUG_SYMPTOMS)} in {comp}", rng.choice((1, 2, 3, 5))
        elif kind == "story":
            title, points = f"Add {rng.choice(STORY_GOALS)} to {comp}", rng.choice((3, 5, 8, 13))
        else:
            title, points = f"{rng.choice(CHORES).capitalize()} for {comp}", rng.choice((1, 2, 3, 5))
        created = start + timedelta(days=rng.randint(0, span - 3))
        due = created + timedelta(days=rng.randint(7, 28))
        age = (end - created).days
        p_done = min(0.9, 0.3 + age / 200)
        rest = (1 - p_done) / 3
        status = rng.choices(("done", "todo", "in_progress", "blocked"),
                             weights=(p_done, rest, rest, rest))[0]
        completed = ""
        if status == "done":
            completed = min(end, created + timedelta(days=rng.randint(2, 30))).isoformat()
        rows.append([
            f"NX-{n:03d}", project, kind, title, rng.choice(workers), status,
            rng.choices(("low", "medium", "high", "critical"), weights=(20, 50, 25, 5))[0],
            points, f"S{(created - start).days // 14 + 1:02d}", created.isoformat(),
            due.isoformat(), completed, rng.choice(NOTES[status]),
        ])
    return rows


# --------------------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------------------


def build_ground_truth(seed: int, months: list[date], plan: dict[str, Any],
                       tag_ids: dict[str, list[str]]) -> dict[str, Any]:
    """Planted errors (20) and decoys, keyed exactly like detector output."""
    errors: list[dict[str, Any]] = []

    def add(kind: str, tool: str, idx: int, ids: list[str]) -> None:
        month = label(months[idx])
        errors.append({"id": f"GT-{len(errors) + 1:02d}", "type": kind, "tool": tool,
                       "month": month, "txn_ids": sorted(ids),
                       "expected_detection_key": f"{kind}|{tool}|{month}"})

    for tool, idx in plan["duplicates"].items():
        add("duplicate_charge", tool, idx, tag_ids[f"dup|{tool}"])
    for tool, (idx, _pct) in plan["price"].items():
        add("price_increase", tool, idx, tag_ids[f"price|{tool}"])
    for tool, idx in plan["unused"].items():
        add("unused_subscription", tool, idx, tag_ids[f"unused|{tool}"])
    for name, idx in plan["unlisted"].items():
        add("unlisted_charge", name, idx, tag_ids[f"unlisted|{name}"])
    for first, second in OVERLAP_PAIRS:
        idx = plan["overlap"][second]
        pair = "+".join(sorted((first, second)))
        ids = tag_ids[f"sw|{first}|{idx}"] + tag_ids[f"sw|{second}|{idx}"]
        add("overlap", pair, idx, ids)

    def decoy(kind: str, tool: str, idx: int, tag: str, false_key: str, note: str) -> dict[str, Any]:
        return {"id": f"DECOY-{kind}", "type": kind, "tool": tool, "month": label(months[idx]),
                "txn_ids": sorted(tag_ids[tag]), "would_be_false_key": false_key, "note": note}

    aws, jet = plan["aws_spike"], plan["jetbrains_renewal"]
    zoom, gap, pro = plan["zoom_change"][0], plan["figma_gap"], plan["slack_prorated"]
    decoys = [
        decoy("usage_spike", "AWS", aws, "decoy|aws_spike",
              f"price_increase|AWS|{label(months[aws])}",
              "Usage-based bill rose ~75%; documented in plan_changes.csv."),
        decoy("annual_renewal", "JetBrains", jet, "decoy|annual_renewal",
              f"price_increase|JetBrains|{label(months[jet])}",
              "Annual licence charged once per year."),
        decoy("recorded_plan_change", "Zoom", zoom, "decoy|plan_change",
              f"price_increase|Zoom|{label(months[zoom])}",
              "Plan upgrade recorded in plan_changes.csv."),
        decoy("short_usage_gap", "Figma", gap, "decoy|usage_gap",
              f"unused_subscription|Figma|{label(months[gap])}",
              "Only 2 months without users (below the 3-month threshold)."),
        decoy("prorated_seat_addition", "Slack", pro, "decoy|prorated_seat",
              f"duplicate_charge|Slack|{label(months[pro])}",
              "Second charge is a small prorated seat add-on, not a duplicate."),
    ]
    assert len(errors) == 20, len(errors)
    return {"seed": seed, "errors": errors, "decoys": decoys}


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------


def generate(seed: int, out_dir: Path) -> dict[str, int]:
    """Generate every file into ``out_dir``; return row counts per file."""
    cfg = load_config()
    months = month_starts(cfg.data.start_month, cfg.data.months)
    end = last_day(months[-1])
    out_dir.mkdir(parents=True, exist_ok=True)

    employees = make_employees(seed, months[0])
    plan = plan_errors(seed, len(months))
    owner_ids = {e["job_title"]: e["name"] for e in reversed(employees)}

    book = TxnBook()
    add_software(book, seed, months, plan)
    add_income(book, seed, months, end)
    add_overheads(book, seed, months, employees)
    txn_rows, tag_ids = finalize_transactions(book, cfg.data.opening_balance_eur * 100)

    emp_header = ["employee_id", "name", "email", "role", "job_title", "department",
                  "hire_date", "gross_monthly_salary"]
    write_csv(out_dir / "employees.csv", emp_header,
              [[e[c] if c != "gross_monthly_salary" else fmt_cents(e[c]) for c in emp_header]
               for e in employees])
    write_csv(out_dir / "subscriptions.csv",
              ["sub_id", "tool", "category", "plan", "pricing_model", "monthly_cost", "seats",
               "billing_day", "owner", "start_month"],
              [[f"S{i:02d}", t.name, t.group, t.plan, t.pricing, fmt_cents(t.cost_cents), t.seats,
                t.billing_day, owner_ids[t.owner_title], label(months[tool_start_index(t, plan)])]
               for i, t in enumerate(TOOLS, start=1)])
    usage = make_usage(seed, months, plan)
    write_csv(out_dir / "usage_log.csv",
              ["tool", "month", "active_users", "logins", "last_used"], usage)
    write_csv(out_dir / "transactions.csv",
              ["txn_id", "date", "description", "category", "counterparty", "amount", "balance"],
              txn_rows)
    tasks = make_tasks(seed, employees, months[0], end, cfg.data.task_count)
    write_csv(out_dir / "tasks.csv",
              ["task_id", "project", "type", "title", "owner", "status", "priority",
               "story_points", "sprint", "created", "due", "completed", "weekly_note"], tasks)

    zoom_idx, zoom_pct = plan["zoom_change"]
    zoom = TOOL_BY_NAME["Zoom"]
    changes = [
        ["PC-01", "Zoom", label(months[zoom_idx]), "plan_change",
         fmt_cents(zoom.cost_cents), fmt_cents(current_cost(zoom, zoom_idx, plan)),
         "Upgraded from Pro to Business for webinar features."],
        ["PC-02", "AWS", label(months[plan["aws_spike"]]), "documented_usage_spike", "", "",
         "Load test and data migration for the LogiDash launch."],
    ]
    write_csv(out_dir / "plan_changes.csv",
              ["change_id", "tool", "month", "change_type", "old_monthly_cost",
               "new_monthly_cost", "note"], changes)

    truth = build_ground_truth(seed, months, plan, tag_ids)
    with (out_dir / "ground_truth.json").open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(truth, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return {"employees": len(employees), "subscriptions": len(TOOLS), "usage_log": len(usage),
            "transactions": len(txn_rows), "tasks": len(tasks), "errors": len(truth["errors"])}


def main() -> None:
    """CLI entry point."""
    cfg = load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=cfg.seed)
    parser.add_argument("--out", type=Path, default=cfg.path("generated"))
    args = parser.parse_args()
    counts = generate(args.seed, args.out)
    print(f"Generated data (seed {args.seed}) in {args.out}")
    for name, n in counts.items():
        print(f"  {name:<14}{n}")


if __name__ == "__main__":
    main()
