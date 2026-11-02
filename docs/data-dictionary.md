# Data dictionary

All data is **synthetic** and produced by `python -m scripts.generate_data` (seed from `config.yaml`, default 42, override with `--seed`). The same seed gives byte-identical files. Window: 12 months starting at `data.start_month` (2025-11 to 2026-10). Currency: EUR. Amounts are negative for money out.

Demo company: Nexora Software Ltd, 15 people, 3 external clients (Brightwave Retail, Pulse Fitness, Northgate Logistics), 4 projects (ShopFlow Web, FitTrack Mobile, LogiDash, plus the internal project Internal Tools, which has no client).

## employees.csv (15 rows)
| column | meaning |
|---|---|
| employee_id | `E001`..`E015` |
| name, email | Faker names, `@nexora.example` addresses (not real) |
| role | `owner` (1), `ops_manager` (1), `pm` (1), `employee` (12): drives access control |
| job_title, department | e.g. Developer (6) + Senior Developer (1), QA Engineer (2), UI/UX Designer, DevOps Engineer, Sales Manager |
| hire_date | before the data window |
| gross_monthly_salary | synthetic; payroll transactions are derived from it (net = 68%, employer contributions = 20% of total gross) |

## subscriptions.csv (18 rows)
`sub_id, tool, category, plan, pricing_model, monthly_cost, seats, billing_day, owner, start_month`
- `pricing_model`: `fixed`, `usage_based` (AWS) or `annual` (JetBrains, charged once a year; `monthly_cost` is the monthly equivalent).
- `start_month`: first month billed. Three tools start mid-window (Linear, Confluence, Microsoft Teams): this creates the planted overlaps.
- `owner`: name of the responsible employee.

## usage_log.csv
`tool, month, active_users, logins, last_used`: one row per billed-or-active tool per month. `active_users = 0` means nobody used the tool; `last_used` is then the last day of the last active month.

## transactions.csv (about 1,000 rows)
`txn_id, date, description, category, counterparty, amount, balance`
- Categories: Income, Payroll (28th), Payroll Taxes, Rent, Insurance, Utilities, Accounting, Bank Fees, Software, Cloud (AWS), Contractors, Hardware, Travel, Office, Reimbursements, Meals, Conferences, Training, Taxes (quarterly).
- Software/Cloud rows use the subscription tool name as `counterparty` and a bank-style description (e.g. `GITHUB *TEAM 15 SEATS`).
- Client invoices are paid on time or 10 to 30 days late (about 20%).
- `balance` is a running balance starting from `data.opening_balance_eur`.

## tasks.csv (250 rows)
`task_id, project, type (bug/story/task), title, owner (employee_id), status (todo/in_progress/blocked/done), priority, story_points, sprint, created, due, completed, weekly_note`.
Generated with Faker-free templates and the seeded RNG. The optional public Jira dataset (Zenodo) is **not** used yet: it is not in `data/raw/`, and its file format has not been verified.

## plan_changes.csv
Recorded, legitimate changes that detectors must respect: a plan upgrade (Zoom) and a documented AWS usage spike.

## ground_truth.json
```json
{"seed": 42, "errors": [...20 items...], "decoys": [...]}
```
Each error: `id, type, tool, month, txn_ids, expected_detection_key` where the key is `<type>|<tool>|<YYYY-MM>`.

| type | count | month means | key tool |
|---|---|---|---|
| duplicate_charge | 5 | month of the double charge | tool |
| price_increase | 5 | first month with the higher charge (+15% to +60%, no plan change) | tool |
| unused_subscription | 4 | first month of a run of 0 active users (run lasts 3+ months, still billed) | tool |
| unlisted_charge | 3 | first month of a recurring charge from a tool not in subscriptions | vendor name |
| overlap | 3 | first month both tools are billed | the two tool names, sorted alphabetically, joined by `+` |

Overlap pairs: Jira+Linear, Confluence+Notion, Microsoft Teams+Slack (the second chat tool is the 18th subscription).

**Decoys** look suspicious but are legitimate; detectors must not flag them. Each has `would_be_false_key`, the key a naive detector would emit:
AWS usage spike (documented), JetBrains annual renewal (charged once), Zoom recorded plan upgrade, Figma 2-month usage gap (below the 3-month rule), Slack prorated seat add-on (small second charge, not a duplicate).

Error months are placed by a seeded RNG, so each seed produces a different layout of the same 20 error types. The evaluation runs seeds 42, 7 and 123.
