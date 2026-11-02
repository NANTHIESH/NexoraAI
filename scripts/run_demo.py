"""Start the API and the Streamlit UI together (used by `make demo`). Ctrl+C stops both."""

from __future__ import annotations

import subprocess
import sys
import time

import httpx

from nexora.config import load_config

STARTUP_WAIT_SECONDS = 60


def main() -> int:
    """Launch uvicorn, wait until /health answers, then run Streamlit in the foreground."""
    cfg = load_config()
    port = cfg.api.base_url.rsplit(":", 1)[-1]
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "nexora.api.main:app", "--port", port])
    try:
        for _ in range(STARTUP_WAIT_SECONDS):
            try:
                if httpx.get(f"{cfg.api.base_url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(1)
        else:
            print("The API did not start in time.", file=sys.stderr)
            return 1
        return subprocess.call([sys.executable, "-m", "streamlit", "run", "app/Home.py"])
    except KeyboardInterrupt:
        return 0
    finally:
        api.terminate()


if __name__ == "__main__":
    sys.exit(main())
