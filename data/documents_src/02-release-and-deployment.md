---
title: "Release and Deployment Rules"
version: "2"
effective_date: 2026-02-01
status: current
owner: DevOps Engineer
format: pdf
---

# Release and Deployment Rules

These rules describe how changes reach production safely.

## Release Process

1. Merge the change to `main` after the review rules in the Engineering Standards are met.
2. The pipeline deploys `main` to the staging environment automatically.
3. QA signs off on staging using the release checklist.
4. The DevOps Engineer or a senior developer tags the release, updates the changelog and deploys to production.

## Deployment Windows

- Production deployments are allowed on Monday to Thursday during working hours.
- On Fridays, deployments are allowed only before 14:00. No production deployments on Fridays after 14:00.
- Deployments are also avoided on the working day before a public holiday, after 14:00.
- The person who deploys stays available for at least two hours afterwards.

## Rollback

- Every release has a rollback plan written in the release checklist.
- If the error rate doubles in the first hour after a deployment, the release is rolled back first and investigated afterwards.
- Database changes must be backward compatible so that the previous version can run against the new schema.

## Emergency Fixes

Fixes for a major incident follow the Incident Response and On-Call document.
