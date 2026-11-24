"""Inbound voice-note transcription through Groq Whisper.

Audio is validated, transcribed in memory and never persisted by this service.
The transcript becomes the normal untrusted user message and still passes through
the agent's grounding and injection boundaries.
"""
from __future__ import annotations

import base64
import re
from fastapi import HTTPException

from backend.config import settings

MAX_AUDIO_BYTES = 16 * 1024 * 1024
AUDIO_TYPES = {
    "audio/webm": "voice.webm",
    "audio/ogg": "voice.ogg",
    "audio/wav": "voice.wav",
    "audio/x-wav": "voice.wav",
    "audio/mp4": "voice.mp4",
    "audio/mpeg": "voice.mp3",
}


def _client():
    import os
    from groq import Groq
    keys = [os.getenv(name) for name in ("GROQ_API_KEY", "GROQ_API_KEY1", "GROQ_API_KEY2_B", "GROQ_API_KEY3_M")]
    key = next((value for value in keys if value), None)
    if not key:
        raise HTTPException(503, "Voice transcription is not configured. Please type your message.")
    return Groq(api_key=key, timeout=40, max_retries=1)


def _decode_audio(audio: str):
    match = re.fullmatch(r"data:(audio/[a-zA-Z0-9.+-]+)(?:;codecs=[^;]*)?;base64,([A-Za-z0-9+/=\r\n]+)", audio or "")
    if not match:
        raise HTTPException(422, "Upload a recorded audio note")
    mime = match[1].lower()
    if mime not in AUDIO_TYPES:
        raise HTTPException(422, "Use WebM, OGG, WAV, MP4 or MP3 audio")
    try:
        data = base64.b64decode(match[2], validate=True)
    except Exception:
        raise HTTPException(422, "The audio note is not valid base64") from None
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "The voice note is too large. Maximum size is 16 MB.")
    return data, mime


def transcribe_audio(audio: str):
    data, mime = _decode_audio(audio)
    client = _client()
    try:
        result = client.audio.transcriptions.create(
            file=(AUDIO_TYPES[mime], data, mime),
            model=settings.stt_model,
            response_format="json",
            temperature=0,
        )
    except Exception:
        raise HTTPException(502, "Transcription failed safely. Please type or resend the voice note.") from None
    text = getattr(result, "text", None) or (result.get("text") if isinstance(result, dict) else "")
    text = " ".join(str(text or "").split())
    if not text:
        raise HTTPException(422, "I could not catch that. Please resend or type your message.")
    language = "bn" if re.search(r"[\u0980-\u09ff]", text) else "en"
    return {"text": text[:4000], "language": language, "model": settings.stt_model}
