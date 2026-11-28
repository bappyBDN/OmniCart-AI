import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def database_url_from_env(environ=None):
    """Require PostgreSQL in containers/Render; retain SQLite for local development."""
    env = os.environ if environ is None else environ
    required = (env.get("RENDER", "").lower() == "true" or
                env.get("DATABASE_REQUIRE_POSTGRES", "").lower() == "true")
    value = (env.get("DATABASE_URL") or "").strip()
    if not value and env.get("POSTGRES_HOST"):
        if not env.get("POSTGRES_PASSWORD"):
            raise RuntimeError("POSTGRES_PASSWORD is required when POSTGRES_HOST is set.")
        try:
            value = URL.create(
                "postgresql+psycopg",
                username=env.get("POSTGRES_USER", "omnicart"),
                password=env["POSTGRES_PASSWORD"],
                host=env["POSTGRES_HOST"],
                port=int(env.get("POSTGRES_PORT", "5432")),
                database=env.get("POSTGRES_DATABASE", "omnichat"),
            ).render_as_string(hide_password=False)
        except ValueError:
            raise RuntimeError("POSTGRES_PORT must be a valid port number.") from None
    if not value:
        if required:
            raise RuntimeError(
                "DATABASE_URL is required for PostgreSQL deployment. On Render, set it "
                "to your Postgres Internal Database URL in the web service Environment. "
                "Docker Compose settings are not applied by Render."
            )
        value = f"sqlite:///{(ROOT / 'ecommerce_saas.db').as_posix()}"
    try:
        url = make_url(value)
        # Accessing .port also validates malformed port values without connecting.
        url.port
    except (ArgumentError, ValueError):
        raise RuntimeError("DATABASE_URL is invalid; supply a PostgreSQL connection URL.") from None
    if required and url.drivername not in ("postgres", "postgresql", "postgresql+psycopg"):
        raise RuntimeError(
            "PostgreSQL is required for this deployment. Replace the SQLite/non-Postgres "
            "DATABASE_URL with your Postgres connection URL."
        )
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = database_url_from_env()
    shop_id: str = os.getenv("SHOP_ID", "shop_01")
    currency: str = os.getenv("SHOP_CURRENCY", "BDT")
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
    # Dedicated embedding service (Phase 3). Disabled by default so the app
    # keeps working with lexical retrieval when no service is configured.
    embedding_service_enabled: bool = os.getenv("EMBEDDING_SERVICE_ENABLED", "false").lower() == "true"
    embedding_service_url: str = os.getenv("EMBEDDING_SERVICE_URL", "")
    embedding_service_api_key: str = os.getenv("EMBEDDING_SERVICE_API_KEY", "")
    embedding_service_timeout: float = float(os.getenv("EMBEDDING_SERVICE_TIMEOUT", "3"))
    # Proxy auth for a private Hugging Face Space. Empty = reuse the HF token,
    # and only for *.hf.space URLs (see embedding_client.service_bearer_token).
    embedding_service_bearer_token: str = os.getenv("EMBEDDING_SERVICE_BEARER_TOKEN", "")
    huggingface_token: str = os.getenv("HUGGINGFACEHUB_API_TOKEN", "")
    embedding_text_dim: int = int(os.getenv("EMBEDDING_TEXT_DIM", "1024"))
    embedding_image_dim: int = int(os.getenv("EMBEDDING_IMAGE_DIM", "768"))
    email_enabled: bool = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
    rag_min_confidence: float = float(os.getenv("RAG_MIN_CONFIDENCE", "0.45"))
    rag_dense_min_similarity: float = float(os.getenv("RAG_DENSE_MIN_SIMILARITY", "0.40"))
    mcp_enabled: bool = os.getenv("MCP_ENABLED", "false").lower() == "true"
    mcp_timeout: float = float(os.getenv("MCP_TIMEOUT", "20"))
    context_max_chars: int = int(os.getenv("CONTEXT_MAX_CHARS", "12000"))
    cors_origins: tuple = tuple(os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","))


settings = Settings()
