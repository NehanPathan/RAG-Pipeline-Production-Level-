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

    # OpenAI-compatible vendors, all served by one provider class
    # (src/llm/providers/openai_compatible_provider.py).
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_small_model: str = "openai/gpt-4o-mini"
    openrouter_large_model: str = "openai/gpt-4o"

    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-10-21"
    # Azure addresses a deployment name, not a model name; the two are often
    # different and mismatching them is the usual cause of a 404 on setup.
    azure_openai_small_deployment: str = "gpt-4o-mini"
    azure_openai_large_deployment: str = "gpt-4o"

    small_llm_provider: str = "openai"
    large_llm_provider: str = "openai"

    # Embeddings
    embedding_provider: str = "openai"  # retrieval role -- openai | bge_m3 | e5_large
    openai_embedding_model: str = "text-embedding-3-large"
    openai_embedding_dimensions: int = 3072
    embedding_batch_size: int = 100
    embedding_max_retries: int = 3

    # Chunking-role embedding (Part 6 dual-role EmbeddingStrategy) -- may
    # differ from `embedding_provider` (the retrieval role) since chunking
    # only needs relative similarity, not the exact vectors stored in Qdrant.
    chunking_embedding_provider: str = "bge_m3"  # bge_m3 | e5_large | openai
    bge_model_name: str = "BAAI/bge-m3"
    e5_model_name: str = "intfloat/e5-large-v2"

    # Reranking
    reranker_provider: str = "bge"
    bge_reranker_model: str = "BAAI/bge-reranker-large"
    cohere_api_key: str = ""
    cohere_reranker_model: str = "rerank-english-v3.0"

    # Document Processing
    default_loader: str = "docling"
    llamaparse_api_key: str = ""
    max_file_size_mb: int = 100
    allowed_file_types: str = "pdf,docx,txt,md,html,png,jpg,jpeg,tiff,bmp"

    # OCR
    ocr_provider: str = "tesseract"  # tesseract | paddle | baidu_unlimited
    ocr_min_words_per_page: float = 10.0
    paddle_ocr_lang: str = "en"
    baidu_ocr_api_key: str = ""
    baidu_ocr_secret_key: str = ""

    # Chunking (ParentChildChunker -- reused inside HybridChunkingPipeline)
    parent_chunk_size: int = 1024
    child_chunk_size: int = 256
    chunk_overlap: int = 32

    # Hybrid Chunking (Part 4) -- SemanticChunker's adaptive threshold and
    # ChunkValidator's rejection thresholds
    semantic_chunk_std_multiplier: float = 1.0
    semantic_chunk_min_sentences: int = 3
    chunk_validator_min_chars: int = 10
    chunk_validator_min_ocr_confidence: float = 0.35

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

    # ------------------------------------------------------------------
    # Governance (NIST AI RMF: Govern / Map / Measure / Manage)
    #
    # These are the machine-readable form of docs/governance/*. They are
    # read by src/governance/policy.py into a frozen AIPolicy that the
    # request path, the evaluation runner, and the CI gate all share, so a
    # threshold is defined in exactly one place.
    # ------------------------------------------------------------------

    # GOVERN
    governance_policy_version: str = "1.0.0"
    # "enforce" blocks violations; "monitor" counts and logs them but lets
    # the request through. Roll new controls out in monitor mode first.
    governance_enforcement_mode: str = "enforce"
    governance_allowed_llm_providers: str = "openai,anthropic,ollama,openrouter,azure"
    governance_allowed_embedding_models: str = (
        "text-embedding-3-large,text-embedding-3-small,BAAI/bge-m3,intfloat/e5-large-v2"
    )
    governance_require_citations: bool = True
    governance_refuse_when_no_context: bool = True

    # MAP -- data classification. `default_sensitivity` is what an unlabelled
    # document is treated as (deliberately not "public"); `default_clearance`
    # is what an unrecognised role may read (deliberately the lowest).
    governance_default_sensitivity: str = "internal"
    governance_default_clearance: str = "public"
    governance_role_clearance: str = (
        "viewer:public,analyst:internal,steward:confidential,admin:restricted"
    )

    # MEASURE -- quality floors shared by the CI gate and the runtime alarm.
    governance_min_faithfulness: float = 0.75
    governance_min_answer_relevancy: float = 0.70
    governance_min_context_relevancy: float = 0.60
    governance_online_eval_sample_rate: float = 0.05
    governance_online_eval_enabled: bool = True
    # Guarantee a trickle of judged samples even on low traffic. At 5%, a
    # few dozen queries a day round to zero samples -- so the dashboard
    # stays empty and drift alerts never fire, which reads as healthy.
    governance_online_eval_min_per_hour: int = 3

    # MANAGE
    governance_retention_days: int = 365
    governance_pii_redaction_enabled: bool = True
    # Where redaction applies. "context" scrubs retrieved passages before the
    # model sees them; "answer" scrubs generated text before the user sees it.
    # Both by default -- context alone still lets the model echo PII it was
    # given earlier in the conversation, and answer alone still ships PII to
    # the LLM provider.
    governance_pii_redact_context: bool = True
    governance_pii_redact_answers: bool = True
    # Redact the user's own question before it is embedded, prompted or
    # stored. Enabled, but deliberately with a *narrow* detector set: the
    # question is also the search key, so masking an email a user is
    # legitimately searching for would break retrieval. The default list
    # covers the values with no search utility and high harm if leaked.
    governance_pii_redact_questions: bool = True
    governance_pii_question_detectors: str = "api_key,credit_card,ssn,iban"
    # Comma-separated detector names to run; empty means all registered.
    governance_pii_detectors: str = ""

    # ------------------------------------------------------------------
    # Feature flags (see src/governance/feature_flags.py)
    #
    # Per-environment defaults. An operator override stored in the database
    # wins over these at runtime; this is the baseline a fresh deployment
    # starts from.
    # ------------------------------------------------------------------
    feature_enable_query_router: bool = True
    feature_enable_web_search: bool = False
    feature_enable_sql_tool: bool = False
    feature_enable_calculator: bool = True
    feature_enable_reranker: bool = True
    feature_enable_guardrails: bool = True
    feature_enable_online_eval: bool = True
    feature_enable_semantic_cache: bool = True
    feature_enable_llm_fallback: bool = True
    feature_answering_enabled: bool = True
    feature_retrieval_enabled: bool = True
    feature_ingestion_enabled: bool = True

    # ------------------------------------------------------------------
    # AI Gateway (src/llm/gateway.py)
    # ------------------------------------------------------------------
    # Ordered fallback chain per role. The gateway tries each in turn on a
    # provider error, so a single vendor outage degrades rather than fails.
    llm_fallback_providers: str = "openai"
    llm_max_retries: int = 2
    llm_timeout_seconds: float = 60.0

    # ------------------------------------------------------------------
    # Query Router (src/routing/)
    # ------------------------------------------------------------------
    # Below this confidence the router falls back to full RAG rather than
    # guessing: answering from the wrong source is worse than a needless
    # retrieval.
    router_min_confidence: float = 0.6
    # Skip the classifier LLM call entirely when a deterministic rule matches.
    router_rules_only: bool = False

    # ------------------------------------------------------------------
    # Tools (src/tools/)
    # ------------------------------------------------------------------
    web_search_provider: str = "tavily"  # tavily | serper | none
    tavily_api_key: str = ""
    serper_api_key: str = ""
    web_search_max_results: int = 5
    # Read-only SQL tool: an explicit table allow-list, never "all tables".
    sql_tool_allowed_tables: str = ""
    sql_tool_max_rows: int = 100

    # ------------------------------------------------------------------
    # Firebase Authentication (src/auth/)
    #
    # The service account JSON must never be committed. Point this at a path
    # outside version control (config/ is git-ignored) or supply the three
    # discrete credential fields via the environment / a secret manager.
    # ------------------------------------------------------------------
    firebase_enabled: bool = False
    firebase_service_account: str = "config/firebase-service-account.json"
    # Browser-facing Web API key, used by the Streamlit UI to sign users in
    # via the Firebase Auth REST API. NOT a secret: it identifies the project
    # and is visible in any browser that talks to Firebase. Data is protected
    # by backend token verification, not by this key's secrecy.
    firebase_web_api_key: str = ""
    firebase_project_id: str = ""
    firebase_client_email: str = ""
    firebase_private_key: str = ""
    firebase_storage_bucket: str = ""
    # Fail closed: when Firebase is enabled, a request without a valid token
    # is rejected. Set false only for a migration window where anonymous
    # traffic must keep working while clients are updated.
    firebase_require_auth: bool = True
    # Email domains allowed to sign in at all. Empty means any verified email.
    firebase_allowed_domains: str = ""
    # New users get this role until an admin promotes them. Deliberately the
    # least privileged one.
    firebase_default_role: str = "viewer"

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

    @staticmethod
    def _csv(raw: str) -> list[str]:
        return [item.strip() for item in (raw or "").split(",") if item.strip()]

    @property
    def llm_fallback_chain(self) -> list[str]:
        """Provider names to try in order. Always non-empty: an empty chain
        would leave the gateway with nothing to call."""
        chain = self._csv(self.llm_fallback_providers)
        return chain or ["openai"]

    @property
    def sql_tool_allowed_tables_list(self) -> list[str]:
        return self._csv(self.sql_tool_allowed_tables)

    @property
    def firebase_allowed_domains_list(self) -> list[str]:
        return [d.lower() for d in self._csv(self.firebase_allowed_domains)]

    @property
    def governance_pii_detectors_list(self) -> list[str]:
        return self._csv(self.governance_pii_detectors)

    @property
    def governance_pii_question_detectors_list(self) -> list[str]:
        return self._csv(self.governance_pii_question_detectors)

    @property
    def firebase_private_key_normalized(self) -> str:
        r"""Restore real newlines in a PEM key carried through the environment.

        A service-account private key is multi-line PEM. Environment variables
        and .env files cannot hold literal newlines, so the key is
        conventionally stored with `\n` escapes -- which the crypto library
        then rejects as malformed. Every deployment hits this once; handling
        it here means nobody has to discover it again.
        """
        return self.firebase_private_key.replace("\\n", "\n")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
