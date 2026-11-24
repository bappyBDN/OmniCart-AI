# core/llm_provider.py
"""
Centralised LLM factory.
Supports Multiple Groq Keys (Primary + Backups) and HuggingFace (Final Fallback).
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
    from langchain_openai import ChatOpenAI
    
    # সব সম্ভাব্য Groq API Key লিস্ট করা হলো
    all_groq_keys = [
        os.getenv("GROQ_API_KEY"),
        os.getenv("GROQ_API_KEY1"),
        os.getenv("GROQ_API_KEY2_B"),
        os.getenv("GROQ_API_KEY3_M")
    ]
    
    # যে Key গুলো .env ফাইলে আছে শুধু সেগুলো ফিল্টার করা
    valid_groq_keys = [key for key in all_groq_keys if key]
    
    if not valid_groq_keys:
        raise EnvironmentError("❌ কোনো GROQ_API_KEY পাওয়া যায়নি!")

    # ১. প্রাইমারি মডেল (প্রথম Groq Key দিয়ে)
    primary_llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.1,
        api_key=valid_groq_keys[0],
        max_tokens=2048,
    )
    
    fallbacks = []
    
    # ২. বাকি Groq Key গুলো দিয়ে Fallback তৈরি করা (Rate limit খেলে যেন অন্য Key ইউজ হয়)
    if len(valid_groq_keys) > 1:
        for backup_key in valid_groq_keys[1:]:
            fallbacks.append(
                ChatGroq(
                    model="llama-3.3-70b-versatile",
                    temperature=0.1,
                    api_key=backup_key,
                    max_tokens=2048,
                )
            )
            
    # ৩. সর্বশেষ ফলব্যাক মডেল (HuggingFace via OpenAI Compatible Endpoint)
    hf_token = os.getenv("HUGGINGFACEHUB_API_TOKEN")
    if hf_token:
        try:
            hf_fallback = ChatOpenAI(
                base_url="https://api-inference.huggingface.co/v1/",
                api_key=hf_token,
                model="Qwen/Qwen2.5-7B-Instruct",
                temperature=0.1,
                max_tokens=1024,
            )
            fallbacks.append(hf_fallback)
        except Exception as e:
            logger.warning(f"⚠️ HuggingFace fallback setup failed: {e}")
            
    # প্রাইমারি মডেলের সাথে সমস্ত ফলব্যাক যুক্ত করে দেওয়া
    if fallbacks:
        return primary_llm.with_fallbacks(fallbacks)
        
    return primary_llm

def get_support_llm():
    return get_sales_llm()

def get_vision_llm():
    from langchain_groq import ChatGroq
    # ভিশন মডেলের জন্য প্রথম ভ্যালিড Key ব্যবহার করা হচ্ছে
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("GROQ_API_KEY1")
    return ChatGroq(
        model="llama-3.2-11b-vision-preview",
        temperature=0.1,
        api_key=api_key,
        max_tokens=1024,
    )

def verify_llm_keys() -> dict:
    # 🆕 টার্মিনালে নতুন Key গুলোর স্ট্যাটাস দেখানোর জন্য আপডেট করা হলো
    keys = {
        "GROQ_API_KEY":      bool(os.getenv("GROQ_API_KEY")),
        "GROQ_API_KEY1":     bool(os.getenv("GROQ_API_KEY1")),
        "GROQ_API_KEY2_B":   bool(os.getenv("GROQ_API_KEY2_B")),
        "GROQ_API_KEY3_M":   bool(os.getenv("GROQ_API_KEY3_M")),
        "HUGGINGFACEHUB_API_TOKEN": bool(os.getenv("HUGGINGFACEHUB_API_TOKEN")),
    }
    for name, present in keys.items():
        if present:
            logger.info(f"✅ {name} is set.")
        else:
            logger.warning(f"⚠️  {name} is MISSING.")
    return keys