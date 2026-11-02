---
title: "Engineering Standards"
version: "3"
effective_date: 2026-01-15
status: current
owner: Engineering Lead
format: md
---

# Engineering Standards

This document covers branching, code review, the definition of done and the open-source licence policy for all Nexora software projects.

## Branching

- The `main` branch is always deployable. Nobody pushes to `main` directly.
- Work happens on short-lived branches named `feature/<ticket>-short-name`, `bugfix/<ticket>-short-name` or `hotfix/<ticket>-short-name`.
- Branches are deleted after merge. Merges to `main` use squash merge so the history stays readable.

## Code Review

- Every pull request needs 2 approvals before merge.
- Authors cannot approve their own pull request. At least one approver must be a developer who has not worked on the change.
- The automated build and tests must pass before a merge is possible.
- Reviewers aim to respond within one working day. Large changes should be split into pull requests that can be reviewed in under an hour.

## Definition of Done

A ticket is done only when all of these are true:

1. The code is merged to `main` after the required approvals.
2. Automated tests cover the change and pass.
3. Documentation and the changelog are updated.
4. QA has checked the acceptance criteria on the staging environment.
5. There are no open critical or high-priority bugs linked to the ticket.

## Open-Source Licence Policy

- Allowed without approval: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause and ISC.
- Needs approval from the Engineering Lead: LGPL and MPL-2.0.
- Not allowed in client products unless the Owner approves in writing: GPL, AGPL and SSPL.
- Code without a clear licence must not be copied into a project.
- Every project keeps an up-to-date licence report of its dependencies.
