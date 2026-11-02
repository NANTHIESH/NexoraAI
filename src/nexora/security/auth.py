"""Local login: bcrypt-hashed passwords and HMAC-signed bearer tokens (demo-grade, no cloud)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass

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


def login(conn: sqlite3.Connection, employee_id: str, password: str, cfg: Config) -> tuple[User, str]:
    """Check the password and return (user, token)."""
    row = conn.execute("SELECT password_hash FROM credentials WHERE employee_id = ?",
                       (employee_id,)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        raise AuthError("Wrong user or password")
    return get_user(conn, employee_id), make_token(employee_id, cfg.auth.token_ttl_minutes)
