---
title: "Information Security: Access, Passwords, Devices and Secrets"
version: "2"
effective_date: 2025-11-01
status: current
owner: DevOps Engineer
format: pdf
---

# Information Security: Access, Passwords, Devices and Secrets

## Access Control

- Access follows the principle of least privilege. People get only the access needed for their role.
- New joiners receive access through a request approved by their manager. Leavers lose all access on their last working day.
- Access to production systems and the company bank account is limited to named people and reviewed every quarter.

## Passwords and Two-Factor Authentication

- All company accounts use the company password manager. Passwords are at least 14 characters and are never reused or shared.
- Two-factor authentication (2FA) is mandatory for email, source control, cloud consoles and the password manager.
- Report a lost phone or a suspected account compromise to the DevOps Engineer immediately.

## Laptops and BYOD

- Company laptops use full-disk encryption, an automatic screen lock after 5 minutes and automatic security updates.
- Personal devices (BYOD) may be used only for email, calendar and chat through approved apps. They must have a screen lock and must not store company files.

## Secrets Handling

- Secrets such as API keys, tokens and passwords never go into source code, tickets or chat messages.
- Secrets are stored in the team secrets manager and injected at deploy time.
- A secret that has been exposed is rotated immediately and the DevOps Engineer is informed.
