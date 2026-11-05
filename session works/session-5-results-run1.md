# Session 5: LangGraph agent on the 5 requests

Model: llama3.2:3b via Ollama (local). Graph: planner -> executor -> writer. All tools are read-only.

## 1. What is our company's annual leave policy?

- Type from Session 1: **Workflow**
- Plan (llm): `search_company_knowledge({"query": "Annual leave policy"})`
- Answer source: llm | time: 41.7s
- Trace: ran search_company_knowledge in 38.7s; number check passed

**Answer:**

Our company's annual leave policy could not be found in the search results. The tool output indicates that the information is not available in the company documents. You may need to ask your manager for more information or check other sources.

## 2. How much money did we spend this month?

- Type from Session 1: **Workflow**
- Plan (llm): `get_financial_summary({"period": "monthly"})`
- Answer source: llm | time: 0.9s
- Trace: ran get_financial_summary in 0.3s; number check passed

**Answer:**

According to the financial summary, the total expenses for this month are €123,408.65.

## 3. Find unused subscriptions, analyse their costs and recommend which ones we should cancel.

- Type from Session 1: **Agent**
- Plan (llm): `get_subscription_usage({"time_period": "monthly", "unused_only": true})`
- Answer source: llm | time: 1.9s
- Trace: ran get_subscription_usage in 0.3s; number check passed

**Answer:**

Based on the tool results, there are 4 unused subscriptions:

1. Postman (cost: 98.0 EUR, 7 seats)
2. Sentry (cost: 26.0 EUR, 7 seats)
3. Datadog (cost: 120.0 EUR, 5 seats)
4. Zoom (cost: 97.93 EUR, 7 seats)

The total cost of these unused subscriptions is 341.93 EUR per month. Canceling any of these subscriptions would result in a monthly saving of 341.93 EUR.

## 4. Prepare a monthly business performance report using our financial data and project updates.

- Type from Session 1: **Agent**
- Plan (llm): `get_financial_summary({"period": "monthly"}) -> get_project_tasks({"status": "all"})`
- Answer source: llm | time: 3.8s
- Trace: ran get_financial_summary in 0.0s; ran get_project_tasks in 0.0s; number check passed

**Answer:**

Here is a monthly business performance report based on the provided financial data and project updates:

**Financial Summary:**

* Period: October 2026
* Income (EUR): 95,000.00
* Expenses (EUR): 123,408.65
* Net (EUR): -28,408.65
* Top expense categories:
	+ Payroll: 58,208.00
	+ Taxes: 22,500.00
	+ Payroll Taxes: 17,120.00
	+ Contractors: 10,500.00
	+ Rent: 3,800.00

**Biggest Cost Increases:**

* Taxes: 22,500.00 (from 0.00)
* Contractors: 4,800.00 (from 5,700.00)
* Training: 750.00 (from 0.00)

**Project Tasks:**

* Total tasks: 250
* Tasks by project:
	+ ShopFlow Web: 92
	+ FitTrack Mobile: 69
	+ LogiDash: 50
	+ Internal Tools: 39
* Tasks with status:
	+ Done: 92
	+ Todo: 17
	+ In Progress: 10
	+ Blocked: 29
	+ Overdue: 73

Please note that a person must approve any changes to the subscription before making any adjustments.

## 5. Check our project updates, identify delayed tasks and suggest what we should do next.

- Type from Session 1: **Agent**
- Plan (llm): `get_project_tasks({"status": "overdue"})`
- Answer source: llm | time: 2.1s
- Trace: ran get_project_tasks in 0.0s; number check passed

**Answer:**

Based on the tool results, there are 73 overdue tasks across 4 projects. The tasks with the most overdue days are:

- NX-173 (FitTrack Mobile): 273 days late
- NX-192 (FitTrack Mobile): 341 days late
- NX-164 (LogiDash): 328 days late
- NX-056 (FitTrack Mobile): 323 days late
- NX-227 (FitTrack Mobile): 320 days late

To proceed, we should prioritize these tasks and allocate resources to complete them as soon as possible. Additionally, we should investigate the reasons for the tasks being blocked (NX-056, NX-227, NX-124, and NX-163) and work with the teams to resolve the dependencies.
