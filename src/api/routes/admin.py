from fastapi import APIRouter
from pydantic import BaseModel

from src.monitoring.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


class SettingValue(BaseModel):
    value: dict | str | int | float | bool


class SettingResponse(BaseModel):
    key: str
    value: dict | str | int | float | bool
    description: str | None = None


DEFAULTS = {
    "small_llm": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    "large_llm": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    "embedding_model": {"provider": "openai", "model": "text-embedding-3-large"},
    "reranker": {"provider": "bge", "model": "BAAI/bge-reranker-large"},
    "vector_top_k": 20,
    "bm25_top_k": 20,
    "rerank_top_n": 10,
    "chunk_parent_size": 1024,
    "chunk_child_size": 256,
    "chunk_overlap": 32,
}


@router.get("/admin/settings")
async def get_settings_all() -> dict:
    return {"settings": [{"key": k, "value": v} for k, v in DEFAULTS.items()]}


@router.put("/admin/settings/{key}")
async def update_setting(key: str, body: SettingValue) -> SettingResponse:
    logger.info("admin_setting_update", key=key, value=str(body.value)[:100])
    return SettingResponse(key=key, value=body.value)
