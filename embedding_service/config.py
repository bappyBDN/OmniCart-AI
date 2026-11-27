"""Environment-only configuration for the embedding service.

No model is loaded at import time; this module is safe to import in tests and
in the main application's venv without torch installed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ServiceSettings:
    api_key: str = field(default_factory=lambda: os.getenv("EMBEDDING_API_KEY", ""))
    text_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_TEXT_MODEL", "BAAI/bge-m3"))
    text_dim: int = field(default_factory=lambda: _int("EMBEDDING_TEXT_DIM", 1024))
    text_revision: str = field(default_factory=lambda: os.getenv("EMBEDDING_TEXT_REVISION", "main"))
    query_max_tokens: int = field(default_factory=lambda: _int("EMBEDDING_QUERY_MAX_TOKENS", 512))
    document_max_tokens: int = field(default_factory=lambda: _int("EMBEDDING_DOCUMENT_MAX_TOKENS", 1024))
    image_model: str = field(default_factory=lambda: os.getenv(
        "EMBEDDING_IMAGE_MODEL", "google/siglip-base-patch16-224"))
    image_dim: int = field(default_factory=lambda: _int("EMBEDDING_IMAGE_DIM", 768))
    image_revision: str = field(default_factory=lambda: os.getenv("EMBEDDING_IMAGE_REVISION", "main"))
    allow_download: bool = field(default_factory=lambda: os.getenv(
        "EMBEDDING_ALLOW_DOWNLOAD", "false").lower() == "true")
    preload: bool = field(default_factory=lambda: os.getenv("EMBEDDING_PRELOAD", "true").lower() == "true")
    request_timeout: float = field(default_factory=lambda: _float("EMBEDDING_REQUEST_TIMEOUT", 20.0))
    max_batch: int = field(default_factory=lambda: _int("EMBEDDING_MAX_BATCH", 64))
    max_text_chars: int = field(default_factory=lambda: _int("EMBEDDING_MAX_TEXT_CHARS", 24000))
    max_image_bytes: int = field(default_factory=lambda: _int("EMBEDDING_MAX_IMAGE_BYTES", 8_000_000))
    max_body_bytes: int = field(default_factory=lambda: _int("EMBEDDING_MAX_BODY_BYTES", 9_000_000))
    torch_threads: int = field(default_factory=lambda: _int("EMBEDDING_TORCH_THREADS", 2))

    def __post_init__(self):
        # Transformers/Hugging Face read these at import time. Setting them here
        # means a runtime container can never silently reach the internet.
        if not self.allow_download:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

    @classmethod
    def from_env(cls) -> "ServiceSettings":
        return cls()

    def model_info(self) -> dict:
        return {
            "text": {"model": self.text_model, "revision": self.text_revision,
                     "dimension": self.text_dim,
                     "query_max_tokens": self.query_max_tokens,
                     "document_max_tokens": self.document_max_tokens},
            "image": {"model": self.image_model, "revision": self.image_revision,
                      "dimension": self.image_dim},
        }


def text_prefix(model_name: str, kind: str) -> str:
    """E5 requires asymmetric prefixes; BGE-M3 does not.

    All other models are passed through untouched so the same service can host
    a shop-specific free model without code changes.
    """
    if "e5" not in (model_name or "").casefold():
        return ""
    return "query: " if kind == "query" else "passage: "
