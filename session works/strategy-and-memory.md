# Session 4: Control strategy and memory

1. **Strategy: plan-and-execute.** For reports and subscription analysis the stages are known in advance (gather data, run the detectors, compute savings, word the summary), so the agent writes the plan first and then runs each step in order.
2. **Check before acting (light reflection).** Python computes every number and the model only words the summary; before a recommendation is shown, its figures are checked against the data, and any write action (for example cancelling a subscription) needs a human to approve it.
3. **Short-term memory: the last 5 turns of the current chat session.** This is what lets a follow-up such as "and last month?" be rewritten into a standalone question (`history_turns: 5`, loaded per user and session); it is dropped when the session ends.
4. **Long-term memory: stored in the database with no expiry.** Chat history (listed per user under Recents), the audit log (question, intent, sources, actions), feedback and confirmed recommendations are kept so conversations can be reopened and every action can be traced.
5. **What it must not remember.** Personal data (emails, IBANs, phone numbers) is redacted before anything is stored, and document contents are retrieved fresh on every question so answers always reflect the current version.

## Notes (for my own understanding, not part of the 5 lines)

- Today the code stores history, audit events and confirmations, but there is **no deletion or retention policy**, so "no expiry" is a gap I should mention if asked. A sensible next step is to delete chat history after a set number of days and keep only the audit log.
- Confirmed recommendations are written to the audit log, but the assistant does **not yet** use them to avoid repeating a suggestion the user already rejected. That would be a good use of long-term memory.
- Plan-and-execute describes the design I am building towards in Session 5 (LangGraph). The current code routes each message to one fixed agent, which is a workflow rather than a planner.
