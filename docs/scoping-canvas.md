# Nexora AI: Scoping Canvas

1. **User:** the office manager of a 12 to 15 person software company (secondary: managers and employees of small businesses with 1 to 50 people).
2. **Problem:** information is scattered across documents, spreadsheets and tools (rules, banking, project updates, subscriptions); people lose time searching and billing mistakes go unnoticed.
3. **Current solution:** manual searches, shared docs, spreadsheets, email updates.
4. **Value:** one assistant that answers rule questions with sources, automates reports, and finds subscription waste.
5. **Data:** synthetic company documents, 12 months of bank transactions, subscription and usage logs, project tasks, employees.
6. **Produces:** cited answers or refusals, weekly/monthly/yearly reports, subscription insights with savings, recommendations, audio summaries.
7. **Technique:** RAG plus multi-agent routing with a local LLM (llama3.2:3b via Ollama), text and audio.
8. **Success metrics and baselines:**
   - answer accuracy on 15 rule questions >90% (baseline: same model without documents)
   - detection recall >=80% with <=3 false alerts on 20 planted errors (baseline: manual review)
   - task time -50% (baseline: manual timing)
   - user satisfaction >85%
   - subscription savings in EUR
9. **Risks:**
   - wrong or outdated answers (mitigation: citations, refusal, superseded-document handling)
   - privacy (local only, RBAC, PII redaction, audit log)
   - over-reliance (recommendations need human confirmation)
   - integration complexity (CSV adapters first)
