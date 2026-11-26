"""Deterministic + model-backed translation helpers for shopping queries.

`to_english` normalises Bangla/Banglish into English keywords so retrieval and
product identification share one vocabulary. `to_bangla` renders an English
assistant answer into natural Bangla when a provider answers in the wrong
script; it is optional and only runs when a chat model is configured.
"""
from backend.language import normalize_query
from backend.retrieval import expand


def to_english(text):
    """Normalise a Bangla/Banglish/English query into English search tokens."""
    if not text or not text.strip():
        return text
    # `expand` already maps Bangla + Banglish shop vocabulary to English.
    return expand(text)


def to_bangla(text, model=None):
    """Translate an English answer into natural Bangla using the chat model."""
    text = (text or "").strip()
    if not text or not model:
        return None
    try:
        response = model.invoke([
            {"role": "system", "content": (
                "You are a Bangla translator for a boutique shop. Translate the "
                "following assistant reply into natural, warm, polite Bengali "
                "(Bangla script). Keep product names, prices (৳), sizes and IDs "
                "unchanged. Match the sentiment; do not add or remove facts."
            )},
            {"role": "user", "content": text},
        ])
        if isinstance(response, dict):
            response = response.get("content") or response.get("text") or ""
        return str(response).strip() if response else None
    except Exception:
        return None


def detect_locale(text):
    return normalize_query(text).locale
