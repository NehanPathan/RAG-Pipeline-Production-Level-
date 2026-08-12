import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from starlette.responses import Response

from src.api.dependencies import (
    ensure_cache_collection,
    ensure_search_schema,
    get_ingestion_pipeline,
)
from src.api.middleware import TraceContextMiddleware
from src.api.routes import (
    admin,
    chat,
    conversations,
    documents,
    evaluation,
    feedback,
    governance,
    health,
    retrieval,
)
from src.config import get_settings
from src.governance.policy import get_policy
from src.governance.rbac import DEFAULT_USER_ID
from src.governance.runtime_flags import get_flags
from src.infrastructure.database.postgres.connection import get_engine, get_session_factory
from src.infrastructure.database.postgres.models import Base, UserModel
from src.monitoring.langsmith import configure_langsmith
from src.monitoring.logger import configure_logging, get_logger
from src.monitoring.prometheus_metrics import policy_info
from src.monitoring.tracing import configure_tracing, instrument_fastapi

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("app_startup", env=settings.app_env)

    # Install the real OTel provider. Without this call every span produced
    # by traced_stage() is non-recording, which is exactly how this project
    # previously shipped a fully-instrumented pipeline that exported nothing.
    if settings.otel_exporter_otlp_endpoint:
        configure_tracing(settings.otel_service_name, settings.otel_exporter_otlp_endpoint)
        logger.info(
            "tracing_configured",
            service=settings.otel_service_name,
            endpoint=settings.otel_exporter_otlp_endpoint,
        )
    else:
        logger.warning("tracing_disabled_no_otlp_endpoint")

    # LangChain exports traces to LangSmith purely on the presence of
    # environment variables. Left alone, an API key set for an evaluation run
    # would silently start shipping prompt text and retrieved document
    # content off-network. The decision is therefore made explicitly here, in
    # both directions.
    configure_langsmith(settings)

    # GOVERN: load and publish the policy in force, so `rag_policy_info`
    # tells a dashboard which rules were active during any time window.
    policy = get_policy()
    policy_info.labels(
        version=policy.version, enforcement_mode=policy.enforcement_mode.value
    ).set(1)
    logger.info("governance_policy_active", **policy.as_dict())

    # Create tables on startup in dev mode
    if settings.app_env == "development":
        engine = get_engine()
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("database_tables_created")
        except IntegrityError:
            # Concurrent uvicorn workers race to create the same tables on
            # first boot; the loser's CREATE TABLE collides with the winner's.
            logger.info("database_tables_already_created")

        # No authentication yet (see risk R-G03) -- ensure the placeholder
        # user that `resolve_principal()` falls back to actually exists, so
        # the documents.user_id FK constraint is satisfied. Its `admin` role
        # is what gives an un-headered dev request full clearance.
        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(
                pg_insert(UserModel)
                .values(id=DEFAULT_USER_ID, email="dev@local", role="admin", is_active=True)
                # No index_elements: concurrent uvicorn workers can race here,
                # and either the `id` or `email` unique constraint may be the
                # one that "loses" the race depending on timing, so any
                # conflict should be a no-op.
                .on_conflict_do_nothing()
            )
            await session.commit()
        logger.info("default_dev_user_ensured", user_id=str(DEFAULT_USER_ID))

        # The semantic query cache is read-before-write on every chat
        # request (see semantic_cache.py), so unlike `document_chunks` it
        # has no natural "first write creates it" trigger and must be
        # ensured here instead.
        await ensure_cache_collection()
        logger.info("cache_collection_ensured")

        # Schema catch-up for both search backends. Qdrant payload indexes
        # and Elasticsearch mapping properties added after the collection or
        # index already existed are otherwise never applied -- silently, in
        # both cases. See ensure_search_schema for why each one is needed.
        await ensure_search_schema()
        logger.info("search_schema_ensured")

        # Warm up the ingestion pipeline (and its embedding models) here,
        # not lazily on the first document upload. `get_ingestion_pipeline()`
        # constructs the chunking-role embedding provider synchronously
        # (SentenceTransformer(...) -- a multi-hundred-MB-to-multi-GB cold
        # download+load for the default bge-m3 model), and it was previously
        # being built as an eager argument to `background_tasks.add_task()`
        # inside the upload route -- meaning the first upload after any
        # container start blocked the entire event loop (all requests, not
        # just that upload) for as long as the model took to download. Found
        # during Phase 4A end-to-end verification. Run via `to_thread` so
        # even this blocking call doesn't tie up the startup event loop.
        await asyncio.to_thread(get_ingestion_pipeline)
        logger.info("ingestion_pipeline_warmed")

    # MANAGE: prime the kill-switch cache and publish it to Prometheus, so
    # `rag_kill_switch_state` reads 1 from the first scrape rather than being
    # absent until the first chat request happens to refresh it.
    flags = await get_flags(force_refresh=True)
    logger.info("runtime_flags_loaded", **flags.as_dict())

    yield

    logger.info("app_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Enterprise Agentic RAG Platform",
        version="1.0.0",
        description="Production-grade RAG with hybrid retrieval and agentic query processing",
        docs_url="/api/docs" if settings.app_env != "production" else None,
        redoc_url="/api/redoc" if settings.app_env != "production" else None,
        lifespan=lifespan,
    )

    # Added before CORS so it is the outermost of the two: the trace id must
    # be bound before any other middleware can log, and must still be set
    # when the response headers are written.
    app.add_middleware(TraceContextMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.app_env == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Browser clients can't read a custom response header unless it is
        # explicitly exposed -- without this the UI can't show users the
        # trace id to quote when reporting a bad answer.
        expose_headers=["X-Trace-Id"],
    )

    instrument_fastapi(app)

    # Routers
    app.include_router(health.router, prefix="/api/v1", tags=["Health"])
    app.include_router(documents.router, prefix="/api/v1", tags=["Documents"])
    app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
    app.include_router(conversations.router, prefix="/api/v1", tags=["Conversations"])
    app.include_router(retrieval.router, prefix="/api/v1", tags=["Retrieval"])
    app.include_router(evaluation.router, prefix="/api/v1", tags=["Evaluation"])
    app.include_router(feedback.router, prefix="/api/v1", tags=["Feedback"])
    app.include_router(governance.router, prefix="/api/v1", tags=["Governance"])
    app.include_router(admin.router, prefix="/api/v1", tags=["Admin"])

    # Prometheus metrics endpoint
    @app.get("/api/v1/metrics", include_in_schema=False)
    async def metrics():
        from src.monitoring.prometheus_metrics import registry
        return Response(
            content=generate_latest(registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    return app


app = create_app()
