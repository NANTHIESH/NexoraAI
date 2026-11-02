"""Request bodies of the Nexora API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MAX_QUESTION_CHARS = 2000


class LoginRequest(BaseModel):
    """Login with an employee id and password."""

    employee_id: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    """One chat message."""

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    session_id: str = Field(default="default", max_length=64)
    include_superseded: bool | None = None  # None: decided from the wording of the question


class FeedbackRequest(BaseModel):
    """Thumbs up (1) or down (-1) on an assistant answer."""

    chat_id: int
    rating: Literal[1, -1]
    comment: str | None = Field(default=None, max_length=1000)


class ActionConfirmation(BaseModel):
    """A human confirms a recommended action (recorded in the audit log; nothing is executed)."""

    tool: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=50)
    detail: str | None = Field(default=None, max_length=500)


class TTSRequest(BaseModel):
    """Text to read aloud."""

    text: str = Field(min_length=1, max_length=5000)
