"""Model loading and vector inference.

Heavy dependencies (torch, sentence-transformers, transformers, PIL) are
imported only inside the loaders. Importing this module remains cheap enough for
the main app's test suite.
"""
from __future__ import annotations

import io
import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import Iterable

from embedding_service.config import ServiceSettings, text_prefix

log = logging.getLogger(__name__)


def _process_rss_mb():
    """Best-effort process RSS without adding psutil to the runtime image."""
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB, macOS reports bytes.
        return round(value / 1024 if sys.platform != "darwin" else value / 1024 / 1024, 1)
    except Exception:
        try:
            with open("/proc/self/status", encoding="ascii") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        return round(int(line.split()[1]) / 1024, 1)
        except Exception:
            return None


class TextBackend:
    def __init__(self, settings: ServiceSettings):
        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(max(1, settings.torch_threads))
        kwargs = {"device": "cpu"}
        if settings.text_revision and not os.path.isdir(settings.text_model):
            kwargs["revision"] = settings.text_revision
        self.settings = settings
        self.model = SentenceTransformer(settings.text_model, **kwargs)
        self.model.max_seq_length = max(settings.query_max_tokens, settings.document_max_tokens)
        self.dimension = int(self.model.get_sentence_embedding_dimension())
        if self.dimension != settings.text_dim:
            raise RuntimeError(
                f"MODEL_DIM_MISMATCH: {settings.text_model} produced {self.dimension}, "
                f"expected {settings.text_dim}"
            )

    def _truncate(self, text: str, max_tokens: int) -> str:
        tokenizer = getattr(self.model, "tokenizer", None)
        if tokenizer is None:
            return text
        ids = tokenizer.encode(text, add_special_tokens=False)
        if len(ids) <= max_tokens:
            return text
        return tokenizer.decode(ids[:max_tokens], skip_special_tokens=True)

    def embed(self, texts: Iterable[str], kind: str) -> list[list[float]]:
        prefix = text_prefix(self.settings.text_model, kind)
        limit = self.settings.query_max_tokens if kind == "query" else self.settings.document_max_tokens
        prepared = [prefix + self._truncate(text, limit) for text in texts]
        vectors = self.model.encode(
            prepared,
            batch_size=min(16, max(1, len(prepared))),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]


class ImageBackend:
    def __init__(self, settings: ServiceSettings):
        import torch
        from transformers import AutoModel, AutoProcessor

        torch.set_num_threads(max(1, settings.torch_threads))
        kwargs = {}
        if settings.image_revision and not os.path.isdir(settings.image_model):
            kwargs["revision"] = settings.image_revision
        self.settings = settings
        self.processor = AutoProcessor.from_pretrained(settings.image_model, **kwargs)
        self.model = AutoModel.from_pretrained(settings.image_model, **kwargs)
        self.model.eval()
        self.dimension = int(settings.image_dim)

    def embed(self, data: bytes) -> list[float]:
        import torch
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 40_000_000
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ("PNG", "JPEG", "WEBP", "GIF"):
                raise ValueError("Unsupported image format")
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            rgb = image.convert("RGB")
            inputs = self.processor(images=rgb, return_tensors="pt")
        with torch.no_grad():
            features = self.model.get_image_features(**inputs)
        features = torch.nn.functional.normalize(features, dim=-1)
        vector = [float(value) for value in features[0].tolist()]
        if len(vector) != self.dimension:
            raise RuntimeError(
                f"MODEL_DIM_MISMATCH: {self.settings.image_model} produced {len(vector)}, "
                f"expected {self.dimension}"
            )
        return vector


@dataclass
class RuntimeState:
    status: str = "loading"
    error: str | None = None
    text_ready: bool = False
    image_ready: bool = False


class EmbeddingRuntime:
    """Thread-safe lazy loader with an explicit ready/loading/error state."""

    def __init__(self, settings: ServiceSettings, text_backend=None, image_backend=None):
        self.settings = settings
        self.text_backend = text_backend
        self.image_backend = image_backend
        self._state = RuntimeState()
        self._lock = threading.Lock()
        self._load_started = False

    @property
    def status(self) -> str:
        return self._state.status

    @property
    def ready(self) -> bool:
        return self._state.status == "ready"

    def load(self):
        with self._lock:
            if self._load_started:
                return
            self._load_started = True
            self._state = RuntimeState(status="loading")
        try:
            if self.text_backend is None:
                self.text_backend = TextBackend(self.settings)
            if self.image_backend is None:
                self.image_backend = ImageBackend(self.settings)
            with self._lock:
                self._state = RuntimeState(status="ready", text_ready=True, image_ready=True)
            log.info("embedding models ready")
        except Exception as exc:
            # Keep the exception type/message; model repo names are not secrets.
            with self._lock:
                self._state = RuntimeState(status="error", error=f"{type(exc).__name__}: {exc}")
            log.exception("embedding model load failed")

    def start_background_load(self):
        thread = threading.Thread(target=self.load, name="embedding-loader", daemon=True)
        thread.start()
        return thread

    def embed_text(self, texts: list[str], kind: str) -> list[list[float]]:
        if not self.ready or self.text_backend is None:
            raise RuntimeError("text model is not ready")
        return self.text_backend.embed(texts, kind)

    def embed_image(self, data: bytes) -> list[float]:
        if not self.ready or self.image_backend is None:
            raise RuntimeError("image model is not ready")
        return self.image_backend.embed(data)

    def health(self) -> dict:
        with self._lock:
            state = RuntimeState(**vars(self._state))
        return {
            "status": state.status,
            "ready": state.status == "ready",
            "error": state.error,
            "models": self.settings.model_info(),
            "memory_rss_mb": _process_rss_mb(),
            "preload": self.settings.preload,
            "allow_download": self.settings.allow_download,
        }
