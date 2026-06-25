from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "development"
    app_secret_key: str = "change-me"
    app_debug: bool = False
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+asyncpg://raguser:ragpass@localhost:5432/ragdb"
    database_pool_size: int = 20
    database_max_overflow: int = 10

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_ttl_embedding: int = 86400
    redis_ttl_query: int = 3600
    redis_ttl_session: int = 3600

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection_name: str = "document_chunks"
    qdrant_cache_collection_name: str = "semantic_query_cache"

    # Elasticsearch
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index_name: str = "document_chunks"
    elasticsearch_username: str = ""
    elasticsearch_password: str = ""

    # LLM Providers
    anthropic_api_key: str = ""
    anthropic_small_model: str = "claude-haiku-4-5-20251001"
    anthropic_large_model: str = "claude-sonnet-4-6"

    openai_api_key: str = ""
    openai_small_model: str = "gpt-4o-mini"
    openai_large_model: str = "gpt-4o"

    google_api_key: str = ""
    gemini_small_model: str = "gemini-1.5-flash"
    gemini_large_model: str = "gemini-1.5-pro"

    ollama_base_url: str = "http://localhost:11434"
    ollama_small_model: str = "llama3.2:3b"
    ollama_large_model: str = "llama3.1:70b"

    small_llm_provider: str = "openai"
    large_llm_provider: str = "openai"

    # Embeddings
    embedding_provider: str = "openai"
    openai_embedding_model: str = "text-embedding-3-large"
    openai_embedding_dimensions: int = 3072
    embedding_batch_size: int = 100
    embedding_max_retries: int = 3

    # Reranking
    reranker_provider: str = "bge"
    bge_reranker_model: str = "BAAI/bge-reranker-large"
    cohere_api_key: str = ""
    cohere_reranker_model: str = "rerank-english-v3.0"

    # Document Processing
    default_loader: str = "docling"
    llamaparse_api_key: str = ""
    max_file_size_mb: int = 100
    allowed_file_types: str = "pdf,docx,txt,md,html"

    # Chunking
    parent_chunk_size: int = 1024
    child_chunk_size: int = 256
    chunk_overlap: int = 32

    # Query Intelligence
    query_expansion_count: int = 3

    # Retrieval
    vector_search_top_k: int = 20
    bm25_search_top_k: int = 20
    rerank_top_n: int = 10
    rrf_k: int = 60

    # Context Processing
    context_max_tokens: int = 6000

    # Semantic Cache
    semantic_cache_score_threshold: float = 0.95

    # Answer Generation
    answer_max_tokens: int = 1024
    answer_temperature: float = 0.3

    # Auth
    jwt_secret_key: str = "change-me-jwt-secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    # Rate Limiting
    rate_limit_chat: int = 30
    rate_limit_upload: int = 10
    rate_limit_default: int = 200

    # Observability
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "prod-rag-api"

    # Streamlit
    api_base_url: str = "http://localhost:8000"

    @property
    def allowed_file_types_list(self) -> list[str]:
        return [t.strip() for t in self.allowed_file_types.split(",")]

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
