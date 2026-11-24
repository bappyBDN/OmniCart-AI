"""Provider-aware token accounting and estimated per-turn cost.

Prices are estimates in USD per million tokens and can be overridden in `.env`.
DeepSeek publishes peak/off-peak pricing; the defaults below use the conservative
peak Flash rate. They are not a substitute for a provider invoice.
"""
from __future__ import annotations

import os


def _rate(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


DEFAULT_RATES = {
    "deepseek": {"input": _rate("DEEPSEEK_INPUT_COST_PER_M", 0.30),
                 "cached": _rate("DEEPSEEK_CACHED_INPUT_COST_PER_M", 0.006),
                 "output": _rate("DEEPSEEK_OUTPUT_COST_PER_M", 1.20)},
    "gpt-oss-120b": {"input": _rate("GROQ_120B_INPUT_COST_PER_M", 0.15),
                     "cached": _rate("GROQ_120B_CACHED_INPUT_COST_PER_M", 0.075),
                     "output": _rate("GROQ_120B_OUTPUT_COST_PER_M", 0.60)},
    "gpt-oss-20b": {"input": _rate("GROQ_20B_INPUT_COST_PER_M", 0.075),
                    "cached": _rate("GROQ_20B_CACHED_INPUT_COST_PER_M", 0.0375),
                    "output": _rate("GROQ_20B_OUTPUT_COST_PER_M", 0.30)},
    "qwen": {"input": _rate("GROQ_QWEN_INPUT_COST_PER_M", 0.80),
             "cached": _rate("GROQ_QWEN_CACHED_INPUT_COST_PER_M", 0.40),
             "output": _rate("GROQ_QWEN_OUTPUT_COST_PER_M", 4.00)},
    "openai": {"input": _rate("OPENAI_INPUT_COST_PER_M", 0.15),
               "cached": _rate("OPENAI_CACHED_INPUT_COST_PER_M", 0.075),
               "output": _rate("OPENAI_OUTPUT_COST_PER_M", 0.60)},
    "unknown": {"input": 0.0, "cached": 0.0, "output": 0.0},
}


def provider_for(model: str) -> str:
    value = (model or "").casefold()
    if "deepseek" in value:
        return "deepseek"
    if "gpt-oss-120b" in value:
        return "gpt-oss-120b"
    if "gpt-oss-20b" in value:
        return "gpt-oss-20b"
    if "qwen" in value:
        return "qwen"
    if value.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    return "unknown"


def rates_for(model: str):
    return DEFAULT_RATES[provider_for(model)]


def empty_usage():
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cached_tokens": 0,
            "calls": 0, "cost_usd": 0.0, "by_model": {}}


def usage_from_message(message, default_model="unknown"):
    usage = getattr(message, "usage_metadata", None) or {}
    metadata = getattr(message, "response_metadata", None) or {}
    token_usage = metadata.get("token_usage") or {}
    input_tokens = int(usage.get("input_tokens") or token_usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or token_usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or token_usage.get("total_tokens") or input_tokens + output_tokens)
    details = usage.get("input_token_details") or {}
    prompt_details = token_usage.get("prompt_tokens_details") or {}
    cached_tokens = int(
        details.get("cache_read") or details.get("cached_tokens")
        or prompt_details.get("cached_tokens") or token_usage.get("prompt_cache_hit_tokens") or 0
    )
    model = metadata.get("model_name") or metadata.get("model") or default_model or "unknown"
    provider = provider_for(model)
    rates = rates_for(model)
    uncached = max(0, input_tokens - cached_tokens)
    cost = (uncached * rates["input"] + cached_tokens * rates["cached"] + output_tokens * rates["output"]) / 1_000_000
    return {
        "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens,
        "cached_tokens": cached_tokens, "calls": 1, "cost_usd": round(cost, 8),
        "model": str(model), "provider": provider,
    }


def merge_usage(existing, record):
    result = empty_usage()
    if isinstance(existing, dict):
        for key in ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "calls", "cost_usd"):
            result[key] = existing.get(key, 0) or 0
        result["by_model"] = dict(existing.get("by_model") or {})
    if not record:
        return result
    for key in ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "calls"):
        result[key] += int(record.get(key, 0) or 0)
    result["cost_usd"] = round(float(result["cost_usd"]) + float(record.get("cost_usd", 0) or 0), 8)
    provider = record.get("provider", "unknown")
    model = record.get("model", "unknown")
    key = f"{provider}:{model}"
    entry = result["by_model"].get(key) or {
        "provider": provider, "model": model, "input_tokens": 0, "output_tokens": 0,
        "total_tokens": 0, "cached_tokens": 0, "calls": 0, "cost_usd": 0.0,
    }
    for field in ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "calls"):
        entry[field] = int(entry.get(field, 0)) + int(record.get(field, 0) or 0)
    entry["cost_usd"] = round(float(entry.get("cost_usd", 0)) + float(record.get("cost_usd", 0) or 0), 8)
    result["by_model"][key] = entry
    return result


def reconcile_usage(usage):
    """Keep the UI's top-level token totals consistent with the model breakdown."""
    if not isinstance(usage, dict) or not usage.get("by_model"):
        return usage
    total = empty_usage()
    for entry in usage["by_model"].values():
        total = merge_usage(total, entry)
    return total


def aggregate_usage(usages):
    """Aggregate persisted turns without counting both the top-level and by-model copies."""
    total = empty_usage()
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        entries = usage.get("by_model") or {}
        if entries:
            for entry in entries.values():
                total = merge_usage(total, entry)
        elif usage.get("provider") and usage.get("model"):
            total = merge_usage(total, usage)
        else:
            total = merge_usage(total, {
                "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0), "cached_tokens": usage.get("cached_tokens", 0),
                "calls": usage.get("calls", 0), "cost_usd": usage.get("cost_usd", 0),
                "provider": "unknown", "model": "legacy",
            })
    return total
