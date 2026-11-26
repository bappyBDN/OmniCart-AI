"""Deterministic + model-backed translation helpers for shopping queries.

`to_english` normalises Bangla/Banglish into English keywords so retrieval and
product identification share one vocabulary. `to_bangla` renders an English
assistant answer into natural Bangla when a provider answers in the wrong
script; it is optional and only runs when a chat model is configured.
"""
import re

from backend.language import normalize_query
from backend.retrieval import expand


def to_english(text):
    """Normalise a Bangla/Banglish/English query into English search tokens."""
    if not text or not text.strip():
        return text
    # `expand` already maps Bangla + Banglish shop vocabulary to English.
    return expand(text)


def _translate(text, model, system):
    text = (text or "").strip()
    if not text or not model:
        return None
    try:
        response = model.invoke([
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ])
        if isinstance(response, dict):
            response = response.get("content") or response.get("text") or ""
        return str(response).strip() if response else None
    except Exception:
        return None


def to_bangla(text, model=None):
    """Translate an English answer into natural Bangla using the chat model."""
    return _translate(text, model, (
        "You are a Bangla translator for a boutique shop. Translate the "
        "following assistant reply into natural, warm, polite Bengali "
        "(Bangla script). Keep product names, prices (৳), sizes and IDs "
        "unchanged. Match the sentiment; do not add or remove facts."
    ))


def to_banglish(text, model=None):
    """Translate an English answer into Banglish (Bengali in Latin letters)."""
    return _translate(text, model, (
        "You are a Banglish translator for a boutique shop. Rewrite the "
        "following assistant reply into natural Banglish (Bengali written in "
        "Latin letters, e.g. 'apnar kurti shundor'). Keep product names, "
        "prices (৳), sizes and IDs unchanged. Match the sentiment; do not add "
        "or remove facts."
    ))


def localize_answer(text, locale, model=None):
    """Guarantee the reply matches the customer's language.

    Bangla and Banglish customers both receive Bengali-script replies (the
    existing agent convention); translate only when a provider answered in a
    different script.
    """
    if not text or not locale or not locale.startswith("bn"):
        return text
    if re.search(r"[\u0980-\u09ff]", text):
        return text  # already Bengali script
    return to_bangla(text, model) or text


def detect_locale(text):
    return normalize_query(text).locale
