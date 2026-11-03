"""Local login: bcrypt-hashed passwords and HMAC-signed bearer tokens (demo-grade, no cloud)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import bcrypt

from nexora.config import Config

_PROCESS_SECRET = secrets.token_hex(32)


@dataclass(frozen=True)
class User:
    """An authenticated employee."""

    employee_id: str
    name: str
    role: str
    job_title: str


class AuthError(Exception):
    """Wrong credentials or an invalid/expired token (the API maps it to HTTP 401)."""


class RegistrationError(Exception):
    """The registration data is not acceptable, e.g. the email is taken (the API maps it to HTTP 409)."""


MIN_PASSWORD_LENGTH = 8
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SELF_REGISTER_ROLE = "employee"  # sign-up never grants more than the least-privileged role


def hash_password(password: str, rounds: int) -> str:
    """bcrypt hash of ``password``."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds)).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time bcrypt check."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _secret() -> bytes:
    return os.environ.get("NEXORA_SECRET", _PROCESS_SECRET).encode()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(employee_id: str, ttl_minutes: int) -> str:
    """Signed token ``base64(employee_id|expiry).signature``."""
    payload = f"{employee_id}|{int(time.time()) + ttl_minutes * 60}"
    return base64.urlsafe_b64encode(payload.encode()).decode() + "." + _sign(payload)


def read_token(token: str) -> str:
    """Return the employee id in a valid, unexpired token; raise :class:`AuthError` otherwise."""
    try:
        encoded, signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded.encode()).decode()
        employee_id, expiry = payload.split("|", 1)
    except ValueError as exc:
        raise AuthError("Invalid token") from exc
    if not hmac.compare_digest(signature, _sign(payload)):
        raise AuthError("Invalid token")
    if int(expiry) < time.time():
        raise AuthError("Token expired, please log in again")
    return employee_id


def get_user(conn: sqlite3.Connection, employee_id: str) -> User:
    """Load an employee as a :class:`User`; raises :class:`AuthError` if unknown."""
    row = conn.execute("SELECT employee_id, name, role, job_title FROM employees WHERE employee_id = ?",
                       (employee_id,)).fetchone()
    if row is None:
        raise AuthError("Unknown user")
    return User(row["employee_id"], row["name"], row["role"], row["job_title"])


# A real bcrypt hash compared against when the user does not exist, so unknown ids and wrong
# passwords take the same time (no user enumeration through response time).
_DUMMY_HASH = bcrypt.hashpw(b"nexora-dummy", bcrypt.gensalt(4)).decode()


def login(conn: sqlite3.Connection, identifier: str, password: str, cfg: Config) -> tuple[User, str]:
    """Check the password and return (user, token). ``identifier`` is an employee id or an email."""
    identifier = identifier.strip()
    row = conn.execute(
        "SELECT c.employee_id, c.password_hash FROM credentials c JOIN employees e USING (employee_id) "
        "WHERE c.employee_id = ? OR lower(e.email) = lower(?)", (identifier, identifier)).fetchone()
    valid = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)
    if row is None or not valid:
        raise AuthError("Wrong user or password")
    return get_user(conn, row["employee_id"]), make_token(row["employee_id"], cfg.auth.token_ttl_minutes)


def register(conn: sqlite3.Connection, name: str, email: str, password: str, cfg: Config) -> tuple[User, str]:
    """Create an employee account with the least-privileged role and return (user, token)."""
    name, email = " ".join(name.split()), email.strip().lower()
    if not name:
        raise RegistrationError("Please enter your name.")
    if not EMAIL_PATTERN.match(email):
        raise RegistrationError("Please enter a valid email address.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise RegistrationError(f"The password must have at least {MIN_PASSWORD_LENGTH} characters.")
    hashed = hash_password(password, cfg.auth.bcrypt_rounds)  # slow step done outside the write transaction
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")  # serialises id generation between concurrent sign-ups
            if conn.execute("SELECT 1 FROM employees WHERE lower(email) = ?", (email,)).fetchone():
                raise RegistrationError("An account with this email already exists. Please log in.")
            last = conn.execute("SELECT MAX(CAST(SUBSTR(employee_id, 2) AS INTEGER)) FROM employees").fetchone()[0]
            employee_id = f"E{(last or 0) + 1:03d}"
            conn.execute("INSERT INTO employees (employee_id, name, email, role, job_title, department, "
                         "hire_date, gross_monthly_salary) VALUES (?,?,?,?,?,?,?,?)",
                         (employee_id, name, email, SELF_REGISTER_ROLE, "Employee", "General",
                          datetime.now(UTC).date().isoformat(), 0.0))
            conn.execute("INSERT INTO credentials (employee_id, password_hash) VALUES (?, ?)",
                         (employee_id, hashed))
    except sqlite3.IntegrityError as exc:
        raise RegistrationError("Could not create the account, please try again.") from exc
    return get_user(conn, employee_id), make_token(employee_id, cfg.auth.token_ttl_minutes)
