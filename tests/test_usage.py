from langchain_core.messages import AIMessage

from backend.usage import aggregate_usage, merge_usage, provider_for, usage_from_message


def message(model, input_tokens=100, output_tokens=20, cached=0):
    return AIMessage(
        content="ok",
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                        "input_token_details": {"cache_read": cached}},
        response_metadata={"model_name": model},
    )


def test_provider_detection_and_estimated_cost():
    assert provider_for("deepseek-flash") == "deepseek"
    assert provider_for("openai/gpt-oss-120b") == "gpt-oss-120b"
    deep = usage_from_message(message("deepseek-flash", 1_000_000, 1_000_000, 0))
    assert deep["provider"] == "deepseek"
    assert deep["cost_usd"] == 1.50
    groq = usage_from_message(message("openai/gpt-oss-20b", 1_000_000, 1_000_000, 0))
    assert groq["cost_usd"] == 0.375


def test_cached_tokens_use_cached_rate_and_merge_by_model():
    deep = usage_from_message(message("deepseek-flash", 1000, 100, 500))
    assert deep["cached_tokens"] == 500
    merged = merge_usage(None, deep)
    assert merged["input_tokens"] == 1000
    assert merged["calls"] == 1
    assert "deepseek:deepseek-flash" in merged["by_model"]
    groq = usage_from_message(message("openai/gpt-oss-120b", 200, 50))
    both = merge_usage(merged, groq)
    assert both["input_tokens"] == 1200
    assert both["output_tokens"] == 150
    assert len(both["by_model"]) == 2


def test_turn_aggregation_does_not_double_count_by_model_entries():
    first = usage_from_message(message("deepseek-flash", 100, 10))
    second = usage_from_message(message("openai/gpt-oss-20b", 50, 5))
    total = aggregate_usage([first, second])
    assert total["input_tokens"] == 150
    assert total["output_tokens"] == 15
    assert total["calls"] == 2
    assert len(total["by_model"]) == 2
