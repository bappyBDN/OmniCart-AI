import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{(ROOT / 'ecommerce_saas.db').as_posix()}")
    shop_id: str = os.getenv("SHOP_ID", "shop_01")
    model: str = os.getenv("CHAT_MODEL", "openai/gpt-oss-120b")
    small_model: str = os.getenv("LLM_SMALL_MODEL", "openai/gpt-oss-20b")
    vision_model: str = os.getenv("VISION_MODEL", "qwen/qwen3.8-27b")
    stt_model: str = os.getenv("STT_MODEL", "whisper-large-v3-turbo")
    deepseek_api_key: str = os.getenv("DEEPSEEK_CHAT_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    deepseek_reasoner_model: str = os.getenv("DEEPSEEK_REASONER_MODEL", "deepseek-reasoner")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    dense_enabled: bool = os.getenv("RAG_DENSE_ENABLED", "true").lower() == "true"
    embedding_download: bool = os.getenv("EMBEDDING_ALLOW_DOWNLOAD", "false").lower() == "true"
    email_enabled: bool = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
    rag_min_confidence: float = float(os.getenv("RAG_MIN_CONFIDENCE", "0.45"))
    rag_dense_min_similarity: float = float(os.getenv("RAG_DENSE_MIN_SIMILARITY", "0.40"))
    mcp_enabled: bool = os.getenv("MCP_ENABLED", "false").lower() == "true"
    mcp_timeout: float = float(os.getenv("MCP_TIMEOUT", "20"))
    context_max_chars: int = int(os.getenv("CONTEXT_MAX_CHARS", "12000"))
    cors_origins: tuple = tuple(os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","))


settings = Settings()
