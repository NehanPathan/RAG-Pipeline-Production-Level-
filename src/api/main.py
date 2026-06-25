from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request
from starlette.responses import Response

from src.api.routes import admin, chat, documents, health, retrieval
from src.config import get_settings
from src.infrastructure.database.postgres.connection import get_engine
from src.infrastructure.database.postgres.models import Base
from src.monitoring.logger import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("app_startup", env=settings.app_env)

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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.app_env == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(health.router, prefix="/api/v1", tags=["Health"])
    app.include_router(documents.router, prefix="/api/v1", tags=["Documents"])
    app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
    app.include_router(retrieval.router, prefix="/api/v1", tags=["Retrieval"])
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
