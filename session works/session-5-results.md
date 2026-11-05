# Session 5: LangGraph agent on the 5 requests

Model: llama3.2:3b via Ollama (local). Graph: planner -> executor -> writer. All tools are read-only.

## 1. What is our company's annual leave policy?

- Type from Session 1: **Workflow**
- Plan (llm): `search_company_knowledge({"query": "What is our company's annual leave policy?"})`
- Answer source: llm | time: 17.2s
- Trace: ran search_company_knowledge in 14.1s; number check passed

**Answer:**

Our company's annual leave policy is as follows:

* Full-time employees get 25 days of paid vacation per year.
* Part-time employees get a proportional number of days.

## 2. How much money did we spend this month?

- Type from Session 1: **Workflow**
- Plan (llm): `get_financial_summary({"period": "monthly"})`
- Answer source: llm | time: 0.8s
- Trace: ran get_financial_summary in 0.3s; number check passed

**Answer:**

The company spent €123,408.65 this month.

## 3. Find unused subscriptions, analyse their costs and recommend which ones we should cancel.

- Type from Session 1: **Agent**
- Plan (llm): `get_subscription_usage({"time_period": "monthly", "unused_only": true})`
- Answer source: llm | time: 1.6s
- Trace: ran get_subscription_usage in 0.2s; number check passed

**Answer:**

**Unused Subscriptions Analysis**

The tool results show that there are 4 unused subscriptions:

* Postman: €98.00/month
* Sentry: €26.00/month
* Datadog: €120.00/month
* Zoom: €97.93/month

The total cost of these unused subscriptions is €341.93 per month.

**Recommendation**

Based on the analysis, it is recommended to cancel these 4 unused subscriptions to save €341.93 per month.

## 4. Prepare a monthly business performance report using our financial data and project updates.

- Type from Session 1: **Agent**
- Plan (llm): `get_financial_summary({"period": "monthly"}) -> get_project_tasks({"status": "all"})`
- Answer source: llm | time: 3.8s
- Trace: ran get_financial_summary in 0.0s; ran get_project_tasks in 0.0s; number check passed

**Answer:**

**Monthly Business Performance Report**

**Financial Summary:**

* Period: October 2026
* Income (EUR): 95,000.00
* Expenses (EUR): 123,408.65
* Net (EUR): -28,408.65
* Top Expense Categories:
	+ Payroll: 58,208.00 EUR
	+ Taxes: 22,500.00 EUR
	+ Payroll Taxes: 17,120.00 EUR
	+ Contractors: 10,500.00 EUR
	+ Rent: 3,800.00 EUR

**Biggest Cost Increases:**

* Taxes: 22,500.00 EUR (from 0.00 EUR in previous period)
* Contractors: 4,800.00 EUR (from 5,700.00 EUR in previous period)
* Training: 750.00 EUR (from 0.00 EUR in previous period)

**Project Tasks:**

* Total Tasks: 250
* Tasks Completed: 167
* Tasks Blocked: 29
* Tasks Overdue: 73
* Tasks by Project:
	+ ShopFlow Web: 92
	+ FitTrack Mobile: 69
	+ LogiDash: 50
	+ Internal Tools: 39
* Tasks by Status:
	+ Done: 167
	+ Todo: 31
	+ Blocked: 29
	+ In Progress: 23

## 5. Check our project updates, identify delayed tasks and suggest what we should do next.

- Type from Session 1: **Agent**
- Plan (llm): `get_project_tasks({"status": "overdue"})`
- Answer source: llm | time: 4.5s
- Trace: ran get_project_tasks in 0.0s; number check passed

**Answer:**

**Delayed Tasks:**

1. **NX-173** (FitTrack Mobile): "Fix missing validation in workout tracker" (due: 2026-01-31, days late: 273)
2. **NX-032** (ShopFlow Web): "Fix timeout in checkout" (due: 2026-06-18, days late: 135)
3. **NX-146** (ShopFlow Web): "Add email digest to order API" (due: 2026-09-25, days late: 36)
4. **NX-192** (FitTrack Mobile): "Add bulk edit to sync service" (due: 2025-11-24, days late: 341)
5. **NX-164** (LogiDash): "Add export to CSV to route map" (due: 2025-12-07, days late: 328)
6. **NX-056** (FitTrack Mobile): "Fix layout glitch in push notifications" (due: 2025-12-12, days late: 323)
7. **NX-227** (FitTrack Mobile): "Add audit trail to push notifications" (due: 2025-12-15, days late: 320)
8. **NX-124** (ShopFlow Web): "Add dark mode to admin panel" (due: 2025-12-16, days late: 319)
9. **NX-163** (FitTrack Mobile): "Add export to CSV to profile" (due: 2025-12-20, days late: 315)

**Next Steps:**

1. Review and prioritize the delayed tasks.
2. Identify the dependencies and blockers for each task.
3. Create a plan to complete the delayed tasks and mitigate the impact on the project timeline.
