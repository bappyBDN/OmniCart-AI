"""Resilient client for the free embedding service.

Retrieval treats an `EmbeddingFallback` as a clean signal to stay on keyword
search. No exception escapes a network failure, so a stopped embedding VM can
never take the shop offline. Query timeout defaults to 3 seconds.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx

from backend.config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddingBatch:
    available: bool
    vectors: list[list[float]]
    model_name: str
    model_version: str
    dimension: int


@dataclass(frozen=True)
class EmbeddingFallback:
    available: bool
    reason: str


class EmbeddingClient:
    def __init__(self, base_url: str = "", api_key: str = "", *, enabled: bool = True,
                 timeout: float = 3.0, max_retries: int = 2, failure_threshold: int = 3,
                 cooldown: float = 30.0, max_batch: int = 64,
                 max_image_bytes: int = 8_000_000, transport: httpx.BaseTransport | None = None,
                 bearer_token: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.enabled = enabled
        self.timeout = max(0.1, float(timeout))
        self.max_retries = max(0, int(max_retries))
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown = max(1.0, float(cooldown))
        self.max_batch = max(1, int(max_batch))
        self.max_image_bytes = max(1, int(max_image_bytes))
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0
        self._last_error = ""
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        # A private Hugging Face Space sits behind HF's proxy, which needs the
        # account token as a Bearer header. X-API-Key is still checked by the
        # service itself, so a leaked Space URL alone is not enough.
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        self._client = httpx.Client(
            base_url=self.base_url or "http://127.0.0.1:1",
            timeout=self.timeout,
            transport=transport,
            headers=headers,
        )

    @property
    def available(self) -> bool:
        if not self.enabled or not self.base_url or not self.api_key:
            return False
        with self._lock:
            return time.monotonic() >= self._open_until

    @property
    def last_error(self) -> str:
        return self._last_error

    def close(self):
        self._client.close()

    def reset(self):
        with self._lock:
            self._failures = 0
            self._open_until = 0.0
            self._last_error = ""

    def _fallback(self, reason: str) -> EmbeddingFallback:
        self._last_error = reason
        return EmbeddingFallback(available=False, reason=reason)

    def _record_failure(self, reason: str):
        with self._lock:
            self._failures += 1
            self._last_error = reason
            if self._failures >= self.failure_threshold:
                self._open_until = time.monotonic() + self.cooldown
                log.warning("embedding circuit opened after %s failures", self._failures)

    def _record_success(self):
        with self._lock:
            self._failures = 0
            self._open_until = 0.0
            self._last_error = ""

    def _request(self, path: str, *, json_body=None, content=None, content_type=None,
                 method: str = "POST"):
        if not self.enabled:
            return self._fallback("disabled")
        if not self.base_url or not self.api_key:
            return self._fallback("not_configured")
        with self._lock:
            if time.monotonic() < self._open_until:
                return self._fallback("circuit_open")

        deadline = time.monotonic() + self.timeout
        last = "unknown"
        headers = {"Content-Type": content_type} if content_type else None
        for attempt in range(self.max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                if method == "GET":
                    response = self._client.get(path, timeout=max(0.05, remaining))
                else:
                    response = self._client.post(
                        path, json=json_body, content=content, headers=headers,
                        timeout=max(0.05, remaining),
                    )
            except httpx.TimeoutException:
                last = "timeout"
            except httpx.HTTPError:
                last = "transport_error"
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    last = f"http_{response.status_code}"
                elif response.status_code >= 400:
                    last = f"http_{response.status_code}"
                    self._record_failure(last)
                    return self._fallback(last)
                else:
                    try:
                        return response.json()
                    except ValueError:
                        last = "invalid_json"
                        self._record_failure(last)
                        return self._fallback(last)
            if attempt < self.max_retries:
                pause = min(0.05 * (2 ** attempt), max(0.0, deadline - time.monotonic()))
                if pause:
                    time.sleep(pause)
        self._record_failure(last)
        return self._fallback(last)

    def health(self):
        payload = self._request("/health", method="GET")
        if isinstance(payload, EmbeddingFallback):
            return payload
        if payload.get("status") != "ready":
            return self._fallback("not_ready")
        self._record_success()
        return payload

    def embed_text(self, texts: list[str], kind: str = "query"):
        if not isinstance(texts, list) or not texts or len(texts) > self.max_batch:
            return self._fallback("invalid_batch")
        if kind not in ("query", "document"):
            return self._fallback("invalid_kind")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            return self._fallback("blank_text")
        payload = self._request("/embed/text", json_body={"texts": texts, "kind": kind})
        if isinstance(payload, EmbeddingFallback):
            return payload
        vectors = payload.get("vectors")
        dimension = payload.get("dimension")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            self._record_failure("invalid_vectors")
            return self._fallback("invalid_vectors")
        if not isinstance(dimension, int) or dimension <= 0:
            self._record_failure("invalid_dimension")
            return self._fallback("invalid_dimension")
        cleaned = []
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != dimension:
                self._record_failure("invalid_dimension")
                return self._fallback("invalid_dimension")
            if any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in vector):
                self._record_failure("invalid_vector_value")
                return self._fallback("invalid_vector_value")
            cleaned.append([float(value) for value in vector])
        self._record_success()
        return EmbeddingBatch(available=True, vectors=cleaned,
                              model_name=str(payload.get("model_name", "")),
                              model_version=str(payload.get("model_version", "")),
                              dimension=dimension)

    def embed_image(self, data: bytes, mime: str = "image/jpeg"):
        if not isinstance(data, (bytes, bytearray)) or not data:
            return self._fallback("empty_image")
        if len(data) > self.max_image_bytes:
            return self._fallback("image_too_large")
        if not isinstance(mime, str) or not mime.startswith("image/"):
            return self._fallback("invalid_mime")
        payload = self._request("/embed/image", content=bytes(data), content_type=mime)
        if isinstance(payload, EmbeddingFallback):
            return payload
        vector = payload.get("vector")
        dimension = payload.get("dimension")
        if not isinstance(vector, list) or not isinstance(dimension, int) or len(vector) != dimension:
            self._record_failure("invalid_dimension")
            return self._fallback("invalid_dimension")
        if any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in vector):
            self._record_failure("invalid_vector_value")
            return self._fallback("invalid_vector_value")
        self._record_success()
        return EmbeddingBatch(available=True, vectors=[[float(value) for value in vector]],
                              model_name=str(payload.get("model_name", "")),
                              model_version=str(payload.get("model_version", "")),
                              dimension=dimension)


def service_bearer_token(url: str, explicit: str = "", hf_token: str = "") -> str:
    """Bearer token for the service proxy: explicit value, else the HF token
    only when the service is a Hugging Face Space (never sent elsewhere)."""
    if explicit:
        return explicit
    host = httpx.URL(url).host if url else ""
    if host.endswith(".hf.space"):
        return hf_token
    return ""


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    return EmbeddingClient(
        settings.embedding_service_url,
        settings.embedding_service_api_key,
        enabled=settings.embedding_service_enabled,
        timeout=settings.embedding_service_timeout,
        bearer_token=service_bearer_token(
            settings.embedding_service_url,
            settings.embedding_service_bearer_token,
            settings.huggingface_token,
        ),
    )
