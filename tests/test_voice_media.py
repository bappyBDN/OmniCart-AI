import base64

import pytest
from fastapi import HTTPException

from backend import voice


def _data(audio=b"fake-audio", mime="audio/webm"):
    return f"data:{mime};base64,{base64.b64encode(audio).decode()}"


def test_product_media_endpoint_returns_labelled_svg_without_leaking_shop(store, client, headers):
    image = client.get("/api/products/SKU_101/image")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in image.text and "illustration" in image.text
    product = client.get("/api/products?q=SKU_101", headers=headers).json()[0]
    assert product["image_url"] == "/api/products/SKU_101/image"
    assert client.get("/api/products/OTHER_SECRET/image").status_code == 404


def test_admin_can_attach_real_product_image_url(client, admin_headers, headers):
    response = client.put("/api/admin/products/SKU_101/image", headers=admin_headers,
                          json={"image_url": "https://cdn.example.com/shirt.jpg", "alt_text": "Cotton shirt"})
    assert response.status_code == 200
    redirect = client.get("/api/products/SKU_101/image", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "https://cdn.example.com/shirt.jpg"
    bad = client.put("/api/admin/products/SKU_101/image", headers=admin_headers,
                     json={"image_url": "javascript:alert(1)", "alt_text": "Bad"})
    assert bad.status_code == 422


def test_voice_transcription_validates_and_returns_text(monkeypatch):
    class Result:
        text = "ei lal shirt ta koto?"
    class Transcriptions:
        def create(self, **kwargs):
            assert kwargs["model"]
            assert kwargs["file"][0] == "voice.webm"
            return Result()
    class FakeClient:
        class Audio:
            transcriptions = Transcriptions()
        audio = Audio()
    monkeypatch.setattr(voice, "_client", lambda: FakeClient())
    result = voice.transcribe_audio(_data())
    assert result["text"] == "ei lal shirt ta koto?"
    assert result["language"] == "en"
    # Browser MediaRecorder includes a codecs parameter in its data URL.
    assert voice.transcribe_audio(_data(mime="audio/webm;codecs=opus"))["text"] == "ei lal shirt ta koto?"


@pytest.mark.parametrize('payload', [
    "not-a-data-url",
    _data(mime="audio/flac"),
    "data:audio/webm;base64,%%%%",
])
def test_voice_audio_validation_rejects_bad_input(payload):
    with pytest.raises(HTTPException):
        voice.transcribe_audio(payload)


def test_voice_endpoint_requires_customer_session_and_handles_mocked_stt(client, headers, monkeypatch):
    import backend.main as main
    monkeypatch.setattr(main, "transcribe_audio", lambda audio: {"text": "hello", "language": "en", "model": "test"})
    response = client.post("/api/voice/transcribe", headers=headers, json={"audio": _data()})
    assert response.status_code == 200
    assert response.json()["text"] == "hello"
    assert client.post("/api/voice/transcribe", json={"audio": _data()}).status_code == 401
