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
    from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint
    
    api_key = os.getenv("GROQ_API_KEY1") or os.getenv("GROQ_API_KEY")
    primary_llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.1,
        api_key=api_key,
        max_tokens=2048,
    )
    
    # Powerful Ungated Fallback (Qwen 2.5 or DeepSeek-Distill)
    hf_token = os.getenv("HUGGINGFACEHUB_API_TOKEN")
    if hf_token:
        try:
            hf_endpoint = HuggingFaceEndpoint(
                repo_id="Qwen/Qwen2.5-7B-Instruct", # Excellent reasoning model, ungated
                huggingfacehub_api_token=hf_token,
                max_new_tokens=1024,
                temperature=0.1,
                task="text-generation"
            )
            backup_llm = ChatHuggingFace(llm=hf_endpoint)
            return primary_llm.with_fallbacks([backup_llm])
        except Exception as e:
            logger.warning(f"⚠️ HuggingFace fallback setup failed: {e}")
            
    return primary_llm

def get_support_llm():
    return get_sales_llm()

def get_vision_llm():
    from langchain_groq import ChatGroq
    api_key = os.getenv("GROQ_API_KEY")
    return ChatGroq(
        model="llama-3.2-11b-vision-preview", # Correct Groq Vision Model
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