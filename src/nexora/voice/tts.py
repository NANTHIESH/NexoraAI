"""Offline text-to-speech with pyttsx3 (optional dependency)."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

MAX_CHARS = 1500
CITATION = re.compile(r"\[[^\[\]]+\]")


class VoiceUnavailableError(RuntimeError):
    """pyttsx3 or a system voice is not available."""


def speakable(text: str) -> str:
    """Drop citations and markdown symbols and shorten the text for reading aloud."""
    text = CITATION.sub("", text)
    text = re.sub(r"[*_`#|]", " ", text)
    return " ".join(text.split())[:MAX_CHARS]


def synthesize(text: str) -> bytes:
    """Return WAV bytes for ``text``; raises :class:`VoiceUnavailableError` if TTS cannot run."""
    try:
        import pyttsx3
    except ImportError as exc:
        raise VoiceUnavailableError(
            "Text-to-speech is not installed. Run: pip install -r requirements-voice.txt") from exc
    spoken = speakable(text)
    if not spoken:
        raise VoiceUnavailableError("There is nothing to read aloud.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "speech.wav"
        try:
            engine = pyttsx3.init()
            engine.save_to_file(spoken, str(path))
            engine.runAndWait()
        except Exception as exc:  # noqa: BLE001 - driver errors differ per OS
            raise VoiceUnavailableError(f"No system voice could be started: {exc}") from exc
        if not path.exists() or path.stat().st_size == 0:
            raise VoiceUnavailableError("The system voice produced no audio.")
        return path.read_bytes()
