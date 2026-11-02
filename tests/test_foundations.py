"""Phase 0 placeholder tests: config loading and setup helpers."""

from scripts.setup_models import model_present, ollama_running

from nexora.config import PROJECT_ROOT, load_config


def test_config_loads_defaults() -> None:
    cfg = load_config()
    assert cfg.seed == 42
    assert cfg.llm.model == "llama3.2:3b"
    assert cfg.embeddings.model.endswith("all-MiniLM-L6-v2")
    assert cfg.retrieval.top_k == 5


def test_paths_resolve_under_project_root() -> None:
    cfg = load_config()
    assert cfg.path("documents") == PROJECT_ROOT / "data" / "documents"


def test_model_present_matches_latest_tag() -> None:
    assert model_present("llama3.2:3b", {"llama3.2:3b"})
    assert model_present("llama3.2", {"llama3.2:latest"})
    assert not model_present("qwen2.5:7b", {"llama3.2:3b"})


def test_ollama_down_is_reported_not_raised() -> None:
    assert ollama_running("http://127.0.0.1:1") is False
