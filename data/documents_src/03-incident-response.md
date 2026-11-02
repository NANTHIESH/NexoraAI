---
title: "Incident Response and On-Call"
version: "2"
effective_date: 2026-01-20
status: current
owner: DevOps Engineer
format: pdf
---

# Incident Response and On-Call

This document defines how Nexora reacts when a production service is degraded or down.

## Severity Levels

- Sev-1: a client-facing production system is down or data is at risk. A Sev-1 incident must be acknowledged within 15 minutes.
- Sev-2: a major feature is broken or severely degraded, with a workaround. Acknowledge within 1 hour.
- Sev-3: a minor problem without client impact. Acknowledge by the next working day.

## On-Call

- One developer and one backup are on call each week. The rotation changes on Monday at 10:00.
- The on-call person keeps a laptop and phone reachable and must be able to log in within the acknowledgement time.
- Handover notes are written in the shared incident channel.

## During an Incident

1. The on-call person acknowledges the alert and becomes the incident commander.
2. The commander opens an incident channel, posts a first status update and informs the Project Manager.
3. The Project Manager informs affected clients using the client communication template.
4. After mitigation, the commander posts a final update.

## After an Incident

A blameless post-incident review is held within 5 working days. It records the timeline, root cause and follow-up actions with owners.
