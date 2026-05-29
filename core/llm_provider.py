# core/llm_provider.py
"""
Centralised LLM factory.
Supports Groq (Primary) and HuggingFace (Fallback) via environment variables.
"""
import os
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

def get_router_llm():
    from langchain_groq import ChatGroq
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY is not set in .env")
    return ChatGroq(model="llama-3.1-8b-instant", temperature=0.0, api_key=api_key, max_tokens=50)

def get_sales_llm():
    from langchain_groq import ChatGroq
    from langchain_openai import ChatOpenAI # 🆕 ম্যাজিক: OpenAI Wrapper ব্যবহার করা হচ্ছে
    
    api_key = os.getenv("GROQ_API_KEY1") or os.getenv("GROQ_API_KEY")
    
    # ১. প্রাইমারি মডেল (Groq)
    primary_llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.1,
        api_key=api_key,
        max_tokens=2048,
    )
    
    # ২. ফলব্যাক মডেল (HuggingFace via OpenAI Compatible Endpoint)
    hf_token = os.getenv("HUGGINGFACEHUB_API_TOKEN")
    if hf_token:
        try:
            # 🔥 THE FIX: HuggingFace-এর v1 API দিয়ে Tool Calling এনাবল করা হলো
            fallback_llm = ChatOpenAI(
                base_url="https://api-inference.huggingface.co/v1/", # HF Endpoint
                api_key=hf_token, # HF Token
                model="Qwen/Qwen2.5-7B-Instruct", # Excellent tool-calling model
                temperature=0.1,
                max_tokens=1024,
            )
            # Groq ফেইল করলে স্বয়ংক্রিয়ভাবে HuggingFace-এ টুল কল চলে যাবে
            return primary_llm.with_fallbacks([fallback_llm])
        except Exception as e:
            logger.warning(f"⚠️ HuggingFace fallback setup failed: {e}")
            
    return primary_llm

def get_support_llm():
    return get_sales_llm()

def get_vision_llm():
    from langchain_groq import ChatGroq
    api_key = os.getenv("GROQ_API_KEY")
    return ChatGroq(
        model="llama-3.2-11b-vision-preview",
        temperature=0.1,
        api_key=api_key,
        max_tokens=1024,
    )

def verify_llm_keys() -> dict:
    keys = {
        "GROQ_API_KEY":  bool(os.getenv("GROQ_API_KEY")),
        "GROQ_API_KEY1": bool(os.getenv("GROQ_API_KEY1")),
        "HUGGINGFACEHUB_API_TOKEN": bool(os.getenv("HUGGINGFACEHUB_API_TOKEN")),
    }
    for name, present in keys.items():
        if present:
            logger.info(f"✅ {name} is set.")
        else:
            logger.warning(f"⚠️  {name} is MISSING.")
    return keys