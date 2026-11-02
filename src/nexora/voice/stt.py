"""Offline speech-to-text with faster-whisper (optional dependency, model tiny by default)."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path

from nexora.config import load_config
from nexora.voice.tts import VoiceUnavailableError

MAX_AUDIO_BYTES = 10 * 1024 * 1024


@lru_cache(maxsize=1)
def _model(name: str):  # noqa: ANN202 - WhisperModel is imported lazily (slow import)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceUnavailableError(
            "Speech-to-text is not installed. Run: pip install -r requirements-voice.txt") from exc
    return WhisperModel(name, device="cpu", compute_type="int8")


def transcribe(audio: bytes) -> str:
    """Transcribe WAV/MP3/OGG audio bytes to text; raises :class:`VoiceUnavailableError` on failure."""
    if not audio:
        raise VoiceUnavailableError("No audio was received.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceUnavailableError("The audio is too large (limit 10 MB).")
    model = _model(load_config().voice.stt_model)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "speech.wav"
        path.write_bytes(audio)
        try:
            segments, _ = model.transcribe(str(path), beam_size=1, language=None)
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception as exc:  # noqa: BLE001 - undecodable audio, model errors
            raise VoiceUnavailableError(f"The audio could not be transcribed: {exc}") from exc
