"""Download both models during the Docker build.

    EMBEDDING_ALLOW_DOWNLOAD=true python -m embedding_service.download_models

At runtime the Dockerfile sets EMBEDDING_ALLOW_DOWNLOAD=false, so the service
fails closed instead of silently fetching a different revision.
"""
import json
import os

from embedding_service.config import ServiceSettings
from embedding_service.runtime import EmbeddingRuntime


def main():
    os.environ.setdefault("EMBEDDING_ALLOW_DOWNLOAD", "true")
    settings = ServiceSettings.from_env()
    runtime = EmbeddingRuntime(settings)
    runtime.load()
    state = runtime.health()
    print(json.dumps(state, ensure_ascii=False, indent=2))
    if state["status"] != "ready":
        raise SystemExit(f"Model download/load failed: {state.get('error')}")


if __name__ == "__main__":
    main()
