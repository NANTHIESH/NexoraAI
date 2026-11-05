# Kickoff pitch (about 2 minutes, roughly 270 words)

Small companies lose time every week. The leave policy is in one document, the spending is in a spreadsheet, the subscriptions are in a bank statement, and the project updates are in someone's email. And billing mistakes, like paying twice or paying for a tool nobody uses, go unnoticed.

Our project is Nexora AI, an assistant for the office manager of a small company. You ask it a question in plain language and it answers from the company's own documents and data, with the source, or it tells you it cannot find the answer.

We built it as an agent. When you ask it to prepare a monthly report, it makes a plan, calls the right tools, one for money, one for project tasks, and writes the result. When you ask which subscriptions to cancel, it finds the unused ones and recommends. It never cancels anything itself: a person has to approve.

It runs entirely on a laptop with a local model. No cloud, no API keys, and the company data stays private. Python computes every number, and the model only puts them into words.

We chose one agent with several tools, not many agents, because our tasks are sequential and a small local model works better with fewer handoffs. If we need separate permissions, for example a project manager who must not see spending, we would split it into specialist agents.

We tested it. It answered 14 of 15 rule questions correctly and found all 20 planted subscription errors. We also found weaknesses: some rephrased prompt injections get past our filter, and personal data can leak into logs. Those are our next fixes, along with a real approval step before any action.

Thank you.
