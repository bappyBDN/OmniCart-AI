"""Bind each provider BEFORE adding fallbacks (Runnable fallbacks cannot bind tools)."""
import os
import threading
from functools import lru_cache

from backend.config import settings

model_status = "cold"
model_lock = threading.Lock()


@lru_cache(maxsize=1)
def http_clients():
    # Reuse TLS/connection pools across backup keys instead of constructing a
    # separate certificate store and socket pool for every provider/key.
    import httpx
    return httpx.Client(timeout=18), httpx.AsyncClient(timeout=18)


@lru_cache(maxsize=4)
def configured_models(vision=False, tier="large"):
    models = []
    from langchain_groq import ChatGroq
    from langchain_openai import ChatOpenAI
    sync_client, async_client = http_clients()
    keys = list(dict.fromkeys(filter(None, [os.getenv(name) for name in (
        "GROQ_API_KEY", "GROQ_API_KEY1", "GROQ_API_KEY2_B", "GROQ_API_KEY3_M")])) )
    # Complex synthesis uses DeepSeek first when configured; routine/routing work
    # stays on the cheaper small Groq model. Vision remains on the Groq vision model.
    deepseek = None
    if settings.deepseek_api_key and not vision:
        deepseek = ChatOpenAI(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            temperature=0,
            max_tokens=2400 if tier == "large" else 1200,
            timeout=30,
            max_retries=0,
            http_client=sync_client,
            http_async_client=async_client,
            http_socket_options=(),
        )
        if tier == "large":
            models.append(deepseek)
    for key in keys:
        model_name = settings.vision_model if vision else settings.small_model if tier == "small" else settings.model
        reasoning = {"reasoning_effort": "low", "reasoning_format": "hidden"} if model_name.startswith("openai/gpt-oss") else {}
        models.append(ChatGroq(model=model_name,
            api_key=key, temperature=0, max_tokens=1200 if tier == "small" else 2400,
            timeout=18, max_retries=0, http_client=sync_client, http_async_client=async_client, **reasoning))
    if os.getenv("OPENAI_API_KEY") and not vision:
        models.append(ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0,
            timeout=18, max_retries=0, max_tokens=1600, http_client=sync_client, http_async_client=async_client,
            http_socket_options=()))
    if os.getenv("HUGGINGFACEHUB_API_TOKEN") and not vision:
        models.append(ChatOpenAI(base_url="https://router.huggingface.co/v1", api_key=os.environ["HUGGINGFACEHUB_API_TOKEN"],
            model=os.getenv("HF_CHAT_MODEL", "Qwen/Qwen2.5-72B-Instruct"), temperature=0, timeout=18, max_retries=0,
            http_client=sync_client, http_async_client=async_client, http_socket_options=()))
    if deepseek is not None and tier == "small":
        models.append(deepseek)
    return models


def warm_models():
    global model_status
    model_status = "warming"
    try:
        with model_lock:
            models = configured_models(False)
        model_status = "ready" if models else "not_configured"
    except Exception:
        model_status = "unavailable"


def chat_model(tools=None, schema=None, vision=False, tier="large", include_raw=False):
    with model_lock:
        models = configured_models(vision, tier) if tier == "small" else configured_models(vision)
    if not models:
        raise RuntimeError("No language model configured")
    bound = [model.bind_tools(tools) if tools else model.with_structured_output(
        schema, method="function_calling", include_raw=include_raw) if schema else model for model in models]
    return bound[0].with_fallbacks(bound[1:]) if len(bound) > 1 else bound[0]
