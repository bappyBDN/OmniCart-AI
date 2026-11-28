"""Resilience tests for the main app's embedding HTTP client."""
import json

import httpx

from backend.embedding_client import EmbeddingBatch, EmbeddingClient, EmbeddingFallback


def transport(handler):
    return httpx.MockTransport(handler)


def test_successful_text_embedding_parses_vectors_and_headers():
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("x-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "vectors": [[0.6, 0.8]], "dimension": 2, "kind": "query",
            "model_name": "BAAI/bge-m3", "model_version": "main",
        })

    client = EmbeddingClient("http://embed", "secret", transport=transport(handler))
    result = client.embed_text(["nil kurti"])
    assert isinstance(result, EmbeddingBatch) and result.available
    assert result.vectors == [[0.6, 0.8]]
    assert result.dimension == 2 and result.model_name == "BAAI/bge-m3"
    assert seen["key"] == "secret" and seen["body"]["kind"] == "query"
    client.close()


def test_http_500_is_retried_and_then_degrades_to_keyword_search():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500, json={"detail": "down"})

    client = EmbeddingClient("http://embed", "secret", max_retries=2,
                             failure_threshold=5, transport=transport(handler))
    result = client.embed_text(["nil kurti"])
    assert isinstance(result, EmbeddingFallback) and not result.available
    assert result.reason == "http_500"
    assert len(calls) == 3
    assert "http_500" in client.last_error
    client.close()


def test_circuit_breaker_stops_calling_a_dead_service():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    client = EmbeddingClient("http://embed", "secret", max_retries=0,
                             failure_threshold=1, cooldown=60, transport=transport(handler))
    first = client.embed_text(["nil kurti"])
    second = client.embed_text(["nil kurti"])
    assert isinstance(first, EmbeddingFallback) and first.reason == "http_503"
    assert isinstance(second, EmbeddingFallback) and second.reason == "circuit_open"
    assert len(calls) == 1
    client.reset()
    assert client.available is True
    client.close()


def test_timeout_never_escapes_as_an_exception():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    client = EmbeddingClient("http://embed", "secret", max_retries=0,
                             failure_threshold=5, transport=transport(handler))
    result = client.embed_text(["nil kurti"])
    assert isinstance(result, EmbeddingFallback) and result.reason == "timeout"
    client.close()


def test_invalid_vector_dimension_is_rejected():
    def handler(request):
        return httpx.Response(200, json={
            "vectors": [[1.0, 2.0, 3.0]], "dimension": 2, "model_name": "x", "model_version": "1",
        })

    client = EmbeddingClient("http://embed", "secret", failure_threshold=5, transport=transport(handler))
    result = client.embed_text(["nil kurti"])
    assert isinstance(result, EmbeddingFallback) and result.reason == "invalid_dimension"
    client.close()


def test_image_embedding_and_size_validation():
    seen = {}

    def handler(request):
        seen["type"] = request.headers.get("content-type")
        seen["bytes"] = request.content
        return httpx.Response(200, json={
            "vector": [0.0, 1.0], "dimension": 2, "model_name": "siglip", "model_version": "main",
        })

    client = EmbeddingClient("http://embed", "secret", max_image_bytes=4, transport=transport(handler))
    result = client.embed_image(b"abcd", "image/png")
    assert isinstance(result, EmbeddingBatch)
    assert result.vectors == [[0.0, 1.0]]
    assert seen == {"type": "image/png", "bytes": b"abcd"}
    too_big = client.embed_image(b"abcde", "image/png")
    assert isinstance(too_big, EmbeddingFallback) and too_big.reason == "image_too_large"
    client.close()


def test_disabled_or_unconfigured_client_returns_a_fallback():
    disabled = EmbeddingClient("http://embed", "secret", enabled=False)
    assert disabled.embed_text(["x"]).reason == "disabled"
    unconfigured = EmbeddingClient("", "")
    assert unconfigured.embed_text(["x"]).reason == "not_configured"
    disabled.close()
    unconfigured.close()


def test_health_uses_get_and_reports_ready():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        return httpx.Response(200, json={"status": "ready", "ready": True})

    client = EmbeddingClient("http://embed", "secret", transport=transport(handler))
    assert client.health()["status"] == "ready"
    assert seen["method"] == "GET"
    client.close()


def test_private_hf_space_gets_bearer_and_api_key_headers():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"vectors": [[1.0]], "dimension": 1,
                                         "model_name": "m", "model_version": "v"})

    client = EmbeddingClient("https://u-omnicart-embeddings.hf.space", "secret",
                             bearer_token="hf_test", transport=transport(handler))
    assert isinstance(client.embed_text(["x"]), EmbeddingBatch)
    assert seen == {"auth": "Bearer hf_test", "key": "secret"}
    client.close()


def test_hf_token_is_only_sent_to_hf_space_hosts():
    from backend.embedding_client import service_bearer_token

    assert service_bearer_token("https://u-x.hf.space", "", "hf_tok") == "hf_tok"
    assert service_bearer_token("https://embed.example.com", "", "hf_tok") == ""
    assert service_bearer_token("https://evil.com/?h=.hf.space", "", "hf_tok") == ""
    assert service_bearer_token("", "", "hf_tok") == ""
    assert service_bearer_token("https://embed.example.com", "explicit", "hf_tok") == "explicit"
