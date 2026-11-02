"""One-time model setup: checks Ollama, pulls the LLM, downloads the embedding model.

Run: python -m scripts.setup_models   (or: make setup)
Never fails hard: without Ollama the app still works in extractive (fallback) mode.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from nexora.config import load_config

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

OLLAMA_INSTALL_STEPS = """\
Ollama was not found / is not running. Install it (free, local):
  1. Download from https://ollama.com/download (Windows, macOS, Linux).
  2. Install and start it (the desktop app starts the server; or run `ollama serve`).
  3. Re-run this script.
Continuing in FALLBACK mode: Nexora will return the best retrieved passage with its citation."""


def ollama_running(base_url: str) -> bool:
    """Return True if the Ollama HTTP server answers at ``base_url``."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        return False


def installed_models(base_url: str) -> set[str]:
    """Names of models already pulled into Ollama."""
    with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as resp:
        data = json.load(resp)
    return {m["name"] for m in data.get("models", [])}


def model_present(model: str, installed: set[str]) -> bool:
    """True if ``model`` is installed (a bare name also matches ``name:latest``)."""
    return model in installed or (":" not in model and f"{model}:latest" in installed)


def pull_llm(model: str) -> bool:
    """Pull ``model`` with the ollama CLI. Returns True on success."""
    exe = shutil.which("ollama")
    if exe is None:
        print("  ollama CLI not on PATH; run `ollama pull " + model + "` manually.")
        return False
    return subprocess.run([exe, "pull", model], check=False).returncode == 0


def download_embedding_model(name: str) -> bool:
    """Download the sentence-transformers model into the local cache."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("  sentence-transformers missing: run `pip install -r requirements.txt`.")
        return False
    SentenceTransformer(name)
    return True


def download_reranker(name: str) -> bool:
    """Download the cross-encoder reranker model into the local cache."""
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        return False
    CrossEncoder(name)
    return True


def main() -> int:
    """Run all checks and print a status summary."""
    cfg = load_config()
    base_url, model = cfg.llm.base_url, cfg.llm.model
    status: dict[str, str] = {}

    print(f"[1/3] Checking Ollama at {base_url}")
    if ollama_running(base_url):
        status["ollama"] = "running"
        print(f"[2/3] Checking LLM {model}")
        if model_present(model, installed_models(base_url)):
            status["llm"] = "already pulled"
        else:
            status["llm"] = "pulled" if pull_llm(model) else "FAILED (fallback mode)"
    else:
        print(OLLAMA_INSTALL_STEPS)
        status["ollama"] = "not running (fallback mode)"
        status["llm"] = "skipped"

    print(f"[3/3] Embedding model {cfg.embeddings.model}")
    ok = download_embedding_model(cfg.embeddings.model)
    status["embeddings"] = "ready" if ok else "FAILED"
    if cfg.reranker.enabled:
        print(f"[+] Reranker model {cfg.reranker.model}")
        status["reranker"] = "ready" if download_reranker(cfg.reranker.model) else "FAILED"

    print("\nStatus:")
    for key, value in status.items():
        print(f"  {key:<11}{value}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
