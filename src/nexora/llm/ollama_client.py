"""Minimal Ollama chat client with clear errors for the two common failure modes."""

from __future__ import annotations

import time

import httpx

from nexora.config import Config, load_config


class OllamaUnavailableError(RuntimeError):
    """Ollama is not installed or not running."""

    def __init__(self, url: str) -> None:
        super().__init__(f"Ollama is not running at {url}. Install it from https://ollama.com, "
                         "start it, then run `make setup`. Until then Nexora answers in "
                         "extractive mode (best passage with its citation).")


class ModelNotPulledError(RuntimeError):
    """The configured model has not been downloaded."""

    def __init__(self, model: str) -> None:
        super().__init__(f"Model '{model}' is not available in Ollama. "
                         f"Run `ollama pull {model}` (or `make setup`).")


READY_CACHE_SECONDS = 10.0
KEEP_ALIVE = "30m"  # keep the model in memory between questions (Ollama default is 5 minutes)
_http = httpx.Client(limits=httpx.Limits(max_keepalive_connections=4))  # reuse connections


class OllamaClient:
    """Chat with a local Ollama model (temperature 0 and a fixed seed by default)."""

    def __init__(self, cfg: Config | None = None) -> None:
        cfg = cfg or load_config()
        self.base_url: str = cfg.llm.base_url
        self.model: str = cfg.llm.model
        self.temperature: float = cfg.llm.temperature
        self.timeout: float = cfg.llm.timeout_seconds
        self.seed: int = cfg.seed
        self._ready: tuple[float, bool] | None = None

    def installed_models(self) -> set[str]:
        """Names of pulled models; raises :class:`OllamaUnavailableError` if not reachable."""
        try:
            resp = _http.get(f"{self.base_url}/api/tags", timeout=3)
            resp.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise OllamaUnavailableError(self.base_url) from exc
        return {m["name"] for m in resp.json().get("models", [])}

    def is_ready(self) -> bool:
        """True if Ollama runs and the configured model is pulled (result cached for a few seconds)."""
        now = time.monotonic()
        if self._ready and now - self._ready[0] < READY_CACHE_SECONDS:
            return self._ready[1]
        ready = self._check_ready()
        self._ready = (now, ready)
        return ready

    def _check_ready(self) -> bool:
        try:
            installed = self.installed_models()
        except OllamaUnavailableError:
            return False
        return self.model in installed or (":" not in self.model and f"{self.model}:latest" in installed)

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        """Single-turn chat; returns the assistant text (valid JSON when ``json_mode``)."""
        payload = {"model": self.model, "stream": False, "keep_alive": KEEP_ALIVE,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                   "options": {"temperature": self.temperature, "seed": self.seed}}
        if json_mode:
            payload["format"] = "json"
        if self.model.startswith("qwen3"):
            payload["think"] = False  # qwen3 reasons first by default (much slower); we compare plain answers
        try:
            resp = _http.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise OllamaUnavailableError(self.base_url) from exc
        if resp.status_code == 404:
            raise ModelNotPulledError(self.model)
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
