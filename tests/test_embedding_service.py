"""Contract tests for the standalone embedding service (no models loaded)."""
import io

from fastapi.testclient import TestClient

from embedding_service.app import create_app
from embedding_service.config import ServiceSettings, text_prefix


class FakeRuntime:
    ready = True

    def health(self):
        return {"status": "ready", "ready": True, "error": None, "memory_rss_mb": 123.0,
                "models": {"text": {"model": "fake-text", "dimension": 2, "revision": "test"},
                           "image": {"model": "fake-image", "dimension": 2, "revision": "test"}}}

    def embed_text(self, texts, kind):
        return [[1.0, 0.0] if kind == "query" else [0.0, 1.0] for _ in texts]

    def embed_image(self, data):
        return [0.5, 0.5]


def make_client(**overrides):
    settings = ServiceSettings(api_key="test-key", text_dim=2, image_dim=2, preload=False,
                               max_batch=2, max_image_bytes=1024, max_body_bytes=4096, **overrides)
    return TestClient(create_app(settings=settings, runtime=FakeRuntime()))


def test_health_is_unaauthenticated_and_reports_ready():
    with make_client() as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["models"]["text"]["dimension"] == 2


def test_embed_text_requires_api_key():
    with make_client() as client:
        assert client.post("/embed/text", json={"texts": ["nil kurti"]}).status_code == 401
        bad = client.post("/embed/text", headers={"X-API-Key": "wrong"}, json={"texts": ["nil kurti"]})
        assert bad.status_code == 401


def test_embed_text_returns_model_version_and_dimension():
    with make_client() as client:
        response = client.post("/embed/text", headers={"X-API-Key": "test-key"},
                               json={"texts": ["nil kurti", "red saree"], "kind": "query"})
    assert response.status_code == 200
    body = response.json()
    assert body["vectors"] == [[1.0, 0.0], [1.0, 0.0]]
    assert body["dimension"] == 2
    assert body["model_name"] == "BAAI/bge-m3"


def test_embed_text_rejects_blank_and_oversized_batches():
    with make_client() as client:
        blank = client.post("/embed/text", headers={"X-API-Key": "test-key"},
                            json={"texts": ["ok", "  "], "kind": "query"})
        assert blank.status_code == 422
        too_many = client.post("/embed/text", headers={"X-API-Key": "test-key"},
                               json={"texts": ["a", "b", "c"], "kind": "query"})
        assert too_many.status_code == 413


def test_embed_image_checks_content_type_and_size():
    with make_client() as client:
        wrong = client.post("/embed/image", headers={"X-API-Key": "test-key", "Content-Type": "text/plain"},
                            content=b"not an image")
        assert wrong.status_code == 415
        too_big = client.post("/embed/image", headers={"X-API-Key": "test-key", "Content-Type": "image/png"},
                              content=b"x" * 2048)
        assert too_big.status_code == 413
        ok = client.post("/embed/image", headers={"X-API-Key": "test-key", "Content-Type": "image/png"},
                         content=b"fake-image")
        assert ok.status_code == 200
        assert ok.json()["vector"] == [0.5, 0.5]


def test_e5_prefixes_are_automatic_and_other_models_are_untouched():
    assert text_prefix("intfloat/multilingual-e5-base", "query") == "query: "
    assert text_prefix("intfloat/multilingual-e5-base", "document") == "passage: "
    assert text_prefix("BAAI/bge-m3", "query") == ""
