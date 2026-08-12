from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.database.postgres.connection import get_engine
from src.infrastructure.database.redis.connection import get_redis_client
from src.monitoring.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

_start_time = datetime.utcnow()


class HealthCheck(BaseModel):
    status: str
    version: str
    uptime_seconds: int
    checks: dict[str, str]
    timestamp: str


@router.get("/health", response_model=HealthCheck)
async def health_check() -> HealthCheck:
    checks = {}

    # PostgreSQL
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"error: {e}"
        logger.warning("health_postgres_fail", error=str(e))

    # Redis
    try:
        redis = get_redis_client()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        logger.warning("health_redis_fail", error=str(e))

    # Qdrant
    try:
        from src.config import get_settings
        from qdrant_client import AsyncQdrantClient
        settings = get_settings()
        qclient = AsyncQdrantClient(url=settings.qdrant_url)
        await qclient.get_collections()
        checks["qdrant"] = "ok"
    except Exception as e:
        checks["qdrant"] = f"error: {e}"
        logger.warning("health_qdrant_fail", error=str(e))

    # Elasticsearch
    try:
        from src.infrastructure.search.elasticsearch.repository import create_elasticsearch_client
        es = create_elasticsearch_client()
        await es.ping()
        await es.close()
        checks["elasticsearch"] = "ok"
    except Exception as e:
        checks["elasticsearch"] = f"error: {e}"
        logger.warning("health_elasticsearch_fail", error=str(e))

    overall = "healthy" if all(v == "ok" for v in checks.values()) else "degraded"
    uptime = int((datetime.utcnow() - _start_time).total_seconds())

    return HealthCheck(
        status=overall,
        version="1.0.0",
        uptime_seconds=uptime,
        checks=checks,
        timestamp=datetime.utcnow().isoformat(),
    )
