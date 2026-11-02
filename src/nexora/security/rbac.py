"""Role-based access control.

Roles (employees.role):
- owner, ops_manager: everything
- pm: documents, all tasks and project reports; no bank/finance data, no payroll
- employee: documents and their own tasks only
"""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    """What a role may do."""

    DOCUMENTS = "documents"            # ask questions about company documents
    OWN_TASKS = "own_tasks"            # see tasks assigned to yourself
    ALL_TASKS = "all_tasks"            # see every task
    PROJECT_REPORTS = "project_reports"  # task/project reports without money figures
    FINANCE = "finance"                # bank transactions, finance reports, payroll
    SUBSCRIPTIONS = "subscriptions"    # subscription analysis and savings


ALL = frozenset(Permission)
ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "owner": ALL,
    "ops_manager": ALL,
    "pm": frozenset({Permission.DOCUMENTS, Permission.OWN_TASKS, Permission.ALL_TASKS,
                     Permission.PROJECT_REPORTS}),
    "employee": frozenset({Permission.DOCUMENTS, Permission.OWN_TASKS}),
}


class PermissionDenied(Exception):
    """The role is not allowed to do this (the API maps it to HTTP 403)."""

    def __init__(self, role: str, permission: Permission) -> None:
        super().__init__(f"Role '{role}' is not allowed to access {permission.value}.")
        self.role, self.permission = role, permission


def can(role: str, permission: Permission) -> bool:
    """True if ``role`` has ``permission`` (unknown roles have none)."""
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def require(role: str, permission: Permission) -> None:
    """Raise :class:`PermissionDenied` unless ``role`` has ``permission``."""
    if not can(role, permission):
        raise PermissionDenied(role, permission)
