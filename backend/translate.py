"""Deterministic + model-backed translation helpers for shopping queries.

`to_english` normalises Bangla/Banglish into English keywords so retrieval and
product identification share one vocabulary. `to_bangla` renders an English
assistant answer into natural Bangla when a provider answers in the wrong
script; it is optional and only runs when a chat model is configured.

`localize_answer` is the safety-critical final step. A translation is accepted
only when every number, currency marker, product/order identifier and citation
from the grounded English answer survives. Otherwise the grounded answer is
returned unchanged.
"""
import re
from collections import Counter

from backend.language import normalize_query
from backend.retrieval import expand

# Bengali letters only. The taka sign (U+09F3) and Bengali digits live in the
# same Unicode block, so a price alone must never make an English answer look
# like it is already in Bengali script.
BENGALI_SCRIPT = re.compile(r"[\u0980-\u09e5\u09f0-\u09f1]")
_BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_NUMBER = re.compile(r"(?<![\w])\d[\d,]*(?:\.\d+)?")
_IDENTIFIER = re.compile(r"\b(?:SKU|IMG|ORD)[-_]\d+\b", re.I)
_CITATION = re.compile(r"\[((?:product|policy|knowledge):[^\]]+)\]")
_CURRENCY = re.compile(r"৳|\$|€|£|₹|\b(?:BDT|USD|EUR|GBP|INR)\b", re.I)

_CURRENCY_CANONICAL = {
    "৳": "BDT", "bdt": "BDT",
    "$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR",
    "£": "GBP", "gbp": "GBP",
    "₹": "INR", "inr": "INR",
}


def to_english(text):
    """Normalise a Bangla/Banglish/English query into English search tokens."""
    if not text or not text.strip():
        return text
    # `expand` already maps Bangla + Banglish shop vocabulary to English.
    return expand(text)


def _content_text(response):
    """Extract text from a LangChain message, bare string, dict or parts list."""
    if response is None:
        return None
    if isinstance(response, str):
        return response.strip() or None
    if isinstance(response, list):
        return _parts_text(response)
    if isinstance(response, dict):
        if "content" in response:
            return _content_text(response["content"])
        if "text" in response:
            return _content_text(response["text"])
        return None
    content = getattr(response, "content", None)
    if content is not None:
        return _content_text(content)
    text = getattr(response, "text", None)
    if text is not None:
        return _content_text(text)
    return None


def _parts_text(parts):
    pieces = []
    for part in parts or []:
        if isinstance(part, str):
            pieces.append(part)
        elif isinstance(part, dict):
            value = part.get("text")
            if value is None and part.get("type") == "text":
                value = part.get("content")
            if value is not None:
                pieces.append(str(value))
        else:
            value = getattr(part, "text", None)
            if value is not None:
                pieces.append(str(value))
    return " ".join(" ".join(pieces).split()) or None


def _invoke(text, model, system):
    text = (text or "").strip()
    if not text or not model:
        return None
    if callable(model) and not hasattr(model, "invoke"):
        try:
            model = model()
        except Exception:
            return None
    try:
        return model.invoke([
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ])
    except Exception:
        return None


def _translate_result(text, model, system):
    """Return (translated_text, raw_response); both are None on failure."""
    response = _invoke(text, model, system)
    if response is None:
        return None, None
    translated = _content_text(response)
    return translated, response


def _translate(text, model, system):
    translated, _ = _translate_result(text, model, system)
    return translated


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


def _numbers(text):
    normal = (text or "").translate(_BENGALI_DIGITS)
    return Counter(number.replace(",", "") for number in _NUMBER.findall(normal))


def _identifiers(text):
    return {value.upper() for value in _IDENTIFIER.findall(text or "")}


def _citations(text):
    return set(_CITATION.findall(text or ""))


def _currencies(text):
    return {_CURRENCY_CANONICAL[token.casefold()] for token in _CURRENCY.findall(text or "")
            if token.casefold() in _CURRENCY_CANONICAL}


def needs_bengali_translation(text):
    """True when a bn/bn-Latn reply is not yet in Bengali letters."""
    return bool(text) and not BENGALI_SCRIPT.search(text)


def preserves_grounding(source, translated):
    """True only when a translation keeps every verifiable fact token.

    Numbers are compared after normalising Bengali digits; citations,
    product/order identifiers and currency *types* must match exactly. The
    translation must also actually be in Bengali script.
    """
    if not translated or not translated.strip() or not BENGALI_SCRIPT.search(translated):
        return False
    if _numbers(source) != _numbers(translated):
        return False
    if _identifiers(source) != _identifiers(translated):
        return False
    if _citations(source) != _citations(translated):
        return False
    return _currencies(source) == _currencies(translated)


def localize_answer(text, locale, model=None, usage_sink=None):
    """Guarantee the reply matches the customer's language.

    Bangla and Banglish customers both receive Bengali-script replies (the
    existing agent convention); translate only when a provider answered in a
    different script. A lossy or failed translation returns the original
    grounded answer. When `usage_sink` is provided it is updated with the
    translation provider's token usage.
    """
    if not text or not locale or not locale.startswith("bn"):
        return text
    if BENGALI_SCRIPT.search(text):
        return text  # already Bengali script
    if model is None:
        return text

    translated, response = _translate_result(text, model, (
        "You are a precise translator for a shop's customer support reply. "
        "Translate the following reply into natural, warm Bengali using "
        "Bengali script. Preserve EVERY number, price, currency symbol, "
        "product ID, SKU, order ID and [source_id] citation exactly. Do not "
        "add, remove, round or reinterpret any fact. Return only the "
        "translation."
    ))
    if not translated or not preserves_grounding(text, translated):
        return text

    if usage_sink is not None and response is not None:
        try:
            from backend.usage import merge_usage, usage_from_message
            record = usage_from_message(response, default_model="translation")
            usage_sink.update(merge_usage(usage_sink, record))
        except Exception:
            pass
    return translated


def detect_locale(text):
    return normalize_query(text).locale
