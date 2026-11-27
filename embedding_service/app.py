"""Small authenticated FastAPI service for free text and image embeddings.

Run with:
    uvicorn embedding_service.app:app --host 0.0.0.0 --port 8080

The service never calls a paid API. Models are downloaded at image build time;
runtime network access is disabled unless EMBEDDING_ALLOW_DOWNLOAD=true.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from embedding_service.config import ServiceSettings
from embedding_service.runtime import EmbeddingRuntime

log = logging.getLogger("embedding_service")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level=logging.INFO):
    # Keep this service's structured logs isolated. Replacing the root logger
    # would make every httpx URL (including provider tokens) visible in tests
    # and in the main app's log stream.
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("embedding_service")
    logger.handlers[:] = [handler]
    logger.setLevel(level)
    logger.propagate = False


class TextRequest(BaseModel):
    texts: list[str] = Field(min_length=1)
    kind: Literal["query", "document"] = "query"

    @field_validator("texts")
    @classmethod
    def texts_are_not_blank(cls, values):
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("texts cannot contain blank entries")
        return cleaned


def create_app(settings: ServiceSettings | None = None, runtime=None) -> FastAPI:
    service_settings = settings or ServiceSettings.from_env()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if runtime is None and service_settings.preload:
            app.state.runtime.start_background_load()
        yield

    app = FastAPI(title="OmniCart Embedding Service", version="1.0.0", lifespan=lifespan)
    app.state.settings = service_settings
    app.state.runtime = runtime or EmbeddingRuntime(service_settings)

    def require_api_key(request: Request):
        expected = service_settings.api_key
        supplied = request.headers.get("X-API-Key", "")
        if not expected or not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(401, "Invalid or missing X-API-Key")

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        started = time.perf_counter()
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                too_large = int(content_length) > service_settings.max_body_bytes
            except ValueError:
                too_large = True
            if too_large:
                response = JSONResponse({"detail": "Request body too large"}, status_code=413)
                response.headers["X-Request-ID"] = request_id
                return response
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request failed", extra={"request_id": request_id})
            raise
        response.headers["X-Request-ID"] = request_id
        log.info("request", extra={"request_id": request_id, "method": request.method,
                                   "path": request.url.path, "status": response.status_code,
                                   "latency_ms": round((time.perf_counter() - started) * 1000, 1)})
        return response

    def ready_or_503():
        if not getattr(app.state.runtime, "ready", False):
            raise HTTPException(503, "Models are still loading")

    @app.get("/health")
    def health():
        payload = app.state.runtime.health()
        payload["service"] = "omnicart-embedding"
        payload["version"] = "1.0.0"
        if payload.get("status") == "cold":
            payload["status"] = "loading"
        return payload

    @app.post("/embed/text", dependencies=[Depends(require_api_key)])
    async def embed_text(payload: TextRequest):
        ready_or_503()
        if len(payload.texts) > service_settings.max_batch:
            raise HTTPException(413, f"At most {service_settings.max_batch} texts per request")
        if any(len(value) > service_settings.max_text_chars for value in payload.texts):
            raise HTTPException(413, "A text exceeds the configured maximum length")
        try:
            vectors = await asyncio.wait_for(
                asyncio.to_thread(app.state.runtime.embed_text, payload.texts, payload.kind),
                timeout=service_settings.request_timeout,
            )
        except asyncio.TimeoutError:
            raise HTTPException(504, "Embedding request timed out") from None
        except Exception:
            log.exception("text embedding failed")
            raise HTTPException(500, "Text embedding failed") from None
        info = service_settings.model_info()["text"]
        return {"vectors": vectors, "dimension": info["dimension"], "kind": payload.kind,
                "model_name": info["model"], "model_version": info["revision"]}

    @app.post("/embed/image", dependencies=[Depends(require_api_key)])
    async def embed_image(request: Request):
        ready_or_503()
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            raise HTTPException(415, "Content-Type must be image/*")
        data = await request.body()
        if not data:
            raise HTTPException(422, "Image body is empty")
        if len(data) > service_settings.max_image_bytes:
            raise HTTPException(413, "Image exceeds the configured maximum size")
        try:
            vector = await asyncio.wait_for(
                asyncio.to_thread(app.state.runtime.embed_image, data),
                timeout=service_settings.request_timeout,
            )
        except asyncio.TimeoutError:
            raise HTTPException(504, "Image embedding timed out") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:
            log.exception("image embedding failed")
            raise HTTPException(500, "Image embedding failed") from None
        info = service_settings.model_info()["image"]
        return {"vector": vector, "dimension": info["dimension"],
                "model_name": info["model"], "model_version": info["revision"]}

    return app


app = create_app()
