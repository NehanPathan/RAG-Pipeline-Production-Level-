from __future__ import annotations

import uuid

from elasticsearch import AsyncElasticsearch

from src.config import get_settings
from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.domain.repositories.search_repository import (
    BM25ScoredChunk,
    BM25SearchFilter,
    SearchRepository,
)
from src.domain.value_objects.sensitivity import Sensitivity
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

INDEX_MAPPINGS = {
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "user_id": {"type": "keyword"},
            "content": {"type": "text", "analyzer": "english"},
            "domain": {"type": "keyword"},
            "tags": {"type": "keyword"},
            "file_type": {"type": "keyword"},
            "document_name": {"type": "keyword"},
            "page_number": {"type": "integer"},
            "section": {"type": "keyword"},
            "contains_table": {"type": "boolean"},
            "token_count": {"type": "integer"},
            "chunk_type": {"type": "keyword"},
            "parent_chunk_id": {"type": "keyword"},
            "sensitivity": {"type": "keyword"},
        }
    },
    "settings": {
        "number_of_shards": 3,
        "number_of_replicas": 1,
    },
}


class ElasticsearchSearchRepository(SearchRepository):
    def __init__(self, client: AsyncElasticsearch, index_name: str) -> None:
        self._client = client
        self._index_name = index_name

    async def create_index_if_not_exists(self) -> None:
        exists = await self._client.indices.exists(index=self._index_name)
        if not exists:
            await self._client.indices.create(
                index=self._index_name,
                mappings=INDEX_MAPPINGS["mappings"],
                settings=INDEX_MAPPINGS["settings"],
            )
            logger.info("elasticsearch_index_created", index=self._index_name)

    async def index_batch(self, chunks: list[DocumentChunk], batch_size: int = 200) -> None:
        if not chunks:
            return
        error_count = 0
        # Large documents can produce hundreds of chunks; bulk-indexing them
        # in one request risks write timeouts, so chunk the request itself.
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            operations = []
            for chunk in batch:
                operations.append({"index": {"_index": self._index_name, "_id": str(chunk.id)}})
                operations.append({
                    "chunk_id": str(chunk.id),
                    "document_id": str(chunk.document_id),
                    "user_id": str(chunk.user_id) if chunk.user_id else None,
                    "content": chunk.content,
                    "chunk_type": chunk.chunk_type.value,
                    "position": chunk.position,
                    "token_count": chunk.token_count,
                    "page_number": chunk.chunk_metadata.page_number,
                    "section": chunk.chunk_metadata.section,
                    "contains_table": chunk.chunk_metadata.contains_table,
                    "parent_chunk_id": str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
                    "domain": chunk.domain,
                    "tags": chunk.tags,
                    "file_type": chunk.file_type,
                    "document_name": chunk.document_name,
                    "sensitivity": chunk.sensitivity.value,
                })
            response = await self._client.bulk(operations=operations, refresh=True)
            if response.get("errors"):
                error_count += len([i for i in response["items"] if "error" in i.get("index", {})])

        if error_count:
            logger.warning("elasticsearch_bulk_errors", count=error_count)
        else:
            logger.info("elasticsearch_indexed", count=len(chunks), index=self._index_name)

    async def search(
        self,
        query: str,
        top_k: int = 20,
        filters: BM25SearchFilter | None = None,
    ) -> list[BM25ScoredChunk]:
        must_clauses: list[dict] = [{"match": {"content": {"query": query}}}]
        filter_clauses = self._build_filter_clauses(filters)

        body = {
            "query": {
                "bool": {
                    "must": must_clauses,
                    "filter": filter_clauses,
                }
            },
            "size": top_k,
        }

        response = await self._client.search(index=self._index_name, body=body)
        hits = response["hits"]["hits"]

        result = []
        for rank, hit in enumerate(hits):
            src = hit["_source"]
            chunk = DocumentChunk(
                id=uuid.UUID(src["chunk_id"]),
                document_id=uuid.UUID(src["document_id"]),
                content=src.get("content", ""),
                position=src.get("position", 0),
                chunk_type=ChunkType(src.get("chunk_type", "child")),
                token_count=src.get("token_count", 0),
                parent_chunk_id=(
                    uuid.UUID(src["parent_chunk_id"]) if src.get("parent_chunk_id") else None
                ),
                chunk_metadata=ChunkMetadata(
                    page_number=src.get("page_number"),
                    section=src.get("section"),
                    contains_table=src.get("contains_table", False),
                ),
                user_id=uuid.UUID(src["user_id"]) if src.get("user_id") else None,
                domain=src.get("domain"),
                tags=src.get("tags") or [],
                file_type=src.get("file_type"),
                document_name=src.get("document_name"),
                sensitivity=Sensitivity.parse(src.get("sensitivity"), Sensitivity.INTERNAL),
            )
            result.append(BM25ScoredChunk(chunk=chunk, bm25_score=hit["_score"], rank=rank + 1))

        return result

    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        try:
            response = await self._client.delete_by_query(
                index=self._index_name,
                query={"term": {"document_id": str(document_id)}},
                refresh=True,
            )
            deleted = response.get("deleted", 0)
        except Exception as exc:
            # Index may not exist if the document failed before search indexing
            logger.warning("elasticsearch_delete_skipped", document_id=str(document_id), error=str(exc))
            return 0
        logger.info("elasticsearch_deleted", document_id=str(document_id), count=deleted)
        return deleted

    def _build_filter_clauses(self, filters: BM25SearchFilter | None) -> list[dict]:
        if filters is None:
            return []
        clauses = []
        if filters.user_id:
            clauses.append({"term": {"user_id": str(filters.user_id)}})
        if filters.domain:
            clauses.append({"term": {"domain": filters.domain}})
        if filters.tags:
            clauses.append({"terms": {"tags": filters.tags}})
        if filters.file_type:
            clauses.append({"term": {"file_type": filters.file_type}})
        if filters.document_ids:
            clauses.append({"terms": {"document_id": [str(d) for d in filters.document_ids]}})
        if filters.sensitivity_in is not None:
            # Mirrors the Qdrant filter: documents indexed before the field
            # existed have no `sensitivity` and must not vanish from results
            # for callers whose clearance covers the INTERNAL default they
            # will be re-checked against by SensitivityGuard.
            terms_clause: dict = {"terms": {"sensitivity": list(filters.sensitivity_in)}}
            if Sensitivity.INTERNAL.value in filters.sensitivity_in:
                clauses.append(
                    {
                        "bool": {
                            "should": [
                                terms_clause,
                                {"bool": {"must_not": {"exists": {"field": "sensitivity"}}}},
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                )
            else:
                clauses.append(terms_clause)
        return clauses


def create_elasticsearch_client() -> AsyncElasticsearch:
    settings = get_settings()
    kwargs: dict = {"hosts": [settings.elasticsearch_url], "request_timeout": 60}
    if settings.elasticsearch_username:
        kwargs["basic_auth"] = (settings.elasticsearch_username, settings.elasticsearch_password)
    return AsyncElasticsearch(**kwargs)
