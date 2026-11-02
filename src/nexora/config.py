"""Central configuration: config.yaml plus .env overrides. No constants live elsewhere."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "NEXORA_SEED": ("seed",),
    "NEXORA_LLM_MODEL": ("llm", "model"),
    "NEXORA_OLLAMA_URL": ("llm", "base_url"),
}


class Config(dict):
    """Dict with attribute-style access to nested sections (``cfg.llm.model``)."""

    def __getattr__(self, name: str) -> Any:
        try:
            value = self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
        return Config(value) if isinstance(value, dict) else value

    def path(self, key: str) -> Path:
        """Resolve a ``paths.<key>`` entry against the project root."""
        return PROJECT_ROOT / self["paths"][key]


def _apply_env(data: dict[str, Any]) -> None:
    for env_name, keys in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        target = data
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = int(raw) if keys == ("seed",) else raw


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> Config:
    """Load config.yaml (or ``path``) and apply .env overrides."""
    load_dotenv(PROJECT_ROOT / ".env")
    cfg_path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    _apply_env(data)
    return Config(data)
