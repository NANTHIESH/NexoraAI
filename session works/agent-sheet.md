# Nexora AI: Agent sheet (Sessions 1 to 6)

## 1. What the agent is for
**User:** the office manager of a 12 to 15 person company.
**Problem:** company rules, money, subscriptions and project updates are scattered, so people lose time searching and billing mistakes go unnoticed.
**Agent:** a local assistant (llama3.2:3b via Ollama, no cloud, no API keys) that answers from company documents and data and recommends actions, but never changes anything by itself.

## 2. Five real requests (Session 1)

| No. | Request | Type | Why |
|---|---|---|---|
| 1 | What is our company's annual leave policy? | Workflow | Fixed path: search the documents, then answer. |
| 2 | How much money did we spend this month? | Workflow | Fixed path: query the transactions, then total them. |
| 3 | Find unused subscriptions, analyse their costs and recommend which ones we should cancel. | Agent | The steps depend on what it finds, and it ends in a recommendation that needs approval. |
| 4 | Prepare a monthly business performance report using our financial data and project updates. | Agent | It chooses and combines several sources into one report. |
| 5 | Check our project updates, identify delayed tasks and suggest what we should do next. | Agent | It decides which tasks count as delayed and what to do about them. |

## 3. Tools (Sessions 2 and 5)

| Tool | Read/Write | Used by requests |
|---|---|---|
| `search_company_knowledge` | Read | 1 |
| `get_subscription_usage` | Read | 3 (also exposed as an MCP tool in `server.py`, Session 3) |
| `get_financial_summary` | Read | 2, 4 |
| `get_project_tasks` | Read | 4, 5 |

No write tool exists yet. Cancelling a subscription is only ever a recommendation; the existing app's "Confirm" button records a decision in the audit log and changes nothing.

**Mismatch to fix before submitting:** the Session 2 file (`Tool use and structured outputs.md`) describes three different tools (`search_company_knowledge`, `get_subscription_usage`, `generate_business_report`). The code has the four above. Either update the Session 2 schemas to match, or say the agent grew from three tools to four.

## 4. Control strategy and memory (Session 4)
- **Strategy:** plan-and-execute. A planner writes the tool calls, an executor runs them, a writer words the answer.
- **Check:** every number comes from Python; the writer's answer is rejected if it contains a figure that is not in the tool results (a weak check: it cannot catch a correct number under the wrong label).
- **Memory:** the last 5 turns of the chat session; chat history, audit log, feedback and confirmations stored in the database, with personal data redacted first. There is no deletion or retention policy yet.

## 5. One agent or several? (Session 6)
**Decision: one agent with several tools.**

Why:
1. Every request is sequential (plan, fetch data, write), which one planner handles.
2. Each extra agent adds model calls and handoffs; the local 3B model is already the weakest part.
3. One agent is easier to test and to secure: permissions and the approval step live in one place.
4. It worked: the LangGraph agent answered all five requests in 1 to 17 seconds (the 17 includes loading the model). Run 1 had real errors (a failed document search and mislabelled numbers); both were fixed and kept as evidence in `session-5-results-run1.md`.

When several agents would be worth it: access control. A finance agent could hold the money tools and a project agent could not, matching the existing roles (a project manager must not see spending). This is a possible next step, not built.

Note on wording: the main app routes each message to one of three agents (knowledge, report, subscription), so the README calls it multi-agent. The Session 5 LangGraph agent is a single agent.

## 6. Evidence so far (from the repository's own result files)
- Rules questions: 14 of 15 correct (93.3%), but the 95% interval is 70% to 99%, so the 90% target is not proven.
- Data answers checked against plain pandas: 159 of 159 correct.
- Detection of 20 planted subscription errors: recall 60 of 60 over three seeds.
- Intent understanding: 85.7% for the model against 54.8% for the keyword router.

## 7. Known limitations (say them out loud)
- Paraphrased prompt injections passed the document guard 10 of 10 times; the model obeyed 1 of 10.
- Personal data leaked into logs in 2 of 12 privacy attacks (an email written in words, a 16-digit number).
- 15% of answerable document questions are wrongly refused.
- The number check is weak, and the writer's "next steps" are generic.
- No real write tool or approval step yet (Session 7).
