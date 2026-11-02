"""Redact emails, IBANs and phone numbers before text is written to logs or the database."""

from __future__ import annotations

import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{2,4}){3,8}\b")
PHONE = re.compile(r"(?<![\w.])(?:\+|00)?\(?\d[\d ()/.\-]{6,}\d(?![\w])")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MIN_PHONE_DIGITS, MAX_PHONE_DIGITS = 8, 15
MIN_IBAN_LENGTH, MAX_IBAN_LENGTH = 15, 34


def _redact_iban(match: re.Match[str]) -> str:
    compact = match.group().replace(" ", "")
    return "[IBAN]" if MIN_IBAN_LENGTH <= len(compact) <= MAX_IBAN_LENGTH else match.group()


def _redact_phone(match: re.Match[str]) -> str:
    text = match.group()
    digits = sum(c.isdigit() for c in text)
    if ISO_DATE.fullmatch(text) or not MIN_PHONE_DIGITS <= digits <= MAX_PHONE_DIGITS:
        return text
    return "[PHONE]"


def redact(text: str) -> str:
    """Replace emails, IBANs and phone numbers with ``[EMAIL]``, ``[IBAN]`` and ``[PHONE]``.

    Order matters: emails and IBANs are removed first so their digits are not read as phones.
    """
    text = EMAIL.sub("[EMAIL]", text)
    text = IBAN.sub(_redact_iban, text)
    return PHONE.sub(_redact_phone, text)
