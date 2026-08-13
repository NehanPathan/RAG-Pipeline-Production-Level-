from __future__ import annotations

import uuid
from typing import Any

from elasticsearch import AsyncElasticsearch

from src.config import get_settings
from src.domain.entities.document import DocumentChunk
from src.domain.repositories.search_repository import (
    BM25ScoredChunk,
    BM25SearchFilter,
    SearchRepository,
)
from src.domain.value_objects.sensitivity import Sensitivity
from src.infrastructure.serialization.chunk_payload import chunk_to_payload, payload_to_chunk
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

INDEX_MAPPINGS: dict[str, Any] = {
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
            "position": {"type": "integer"},
            "section": {"type": "keyword"},
            "section_title": {"type": "keyword"},
            "heading_level": {"type": "integer"},
            "contains_table": {"type": "boolean"},
            "token_count": {"type": "integer"},
            "chunk_type": {"type": "keyword"},
            "parent_chunk_id": {"type": "keyword"},
            "sensitivity": {"type": "keyword"},
            "entity_canonicals": {"type": "keyword"},
            "content_kind": {"type": "keyword"},
            "project_id": {"type": "keyword"},
            "project_number": {"type": "keyword"},
            "drawing_id": {"type": "keyword"},
            "drawing_number": {"type": "keyword"},
            "revision_label": {"type": "keyword"},
            "is_latest": {"type": "boolean"},
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

    async def ensure_mapping(self) -> None:
        """Push new mapping properties onto an index that already exists.

        `create_index_if_not_exists` short-circuits when the index is there,
        so editing INDEX_MAPPINGS has no effect on any deployment that has
        already ingested a document — the new field gets dynamically mapped
        at best and silently mis-typed at worst. Called from startup; a
        put_mapping that only adds properties is safe to run repeatedly.
        """
        try:
            await self._client.indices.put_mapping(
                index=self._index_name,
                properties=INDEX_MAPPINGS["mappings"]["properties"],
            )
        except Exception as exc:
            # Missing index is the normal case on a fresh deployment;
            # create_index_if_not_exists will apply the full mapping instead.
            logger.info("elasticsearch_mapping_skipped", index=self._index_name, error=str(exc))
            return
        logger.info("elasticsearch_mapping_ensured", index=self._index_name)

    async def index_batch(self, chunks: list[DocumentChunk], batch_size: int = 200) -> None:
        if not chunks:
            return
        error_count = 0
        # Large documents can produce hundreds of chunks; bulk-indexing them
        # in one request risks write timeouts, so chunk the request itself.
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            operations: list[dict[str, Any]] = []
            for chunk in batch:
                operations.append({"index": {"_index": self._index_name, "_id": str(chunk.id)}})
                operations.append(chunk_to_payload(chunk))
            response = await self._client.bulk(operations=operations, refresh=True)
            if response.get("errors"):
                error_count += len([i for i in response["items"] if "error" in i.get("index", {})])

        if error_count:
            logger.warning("elasticsearch_bulk_errors", count=error_count)
        else:
            logger.info("elasticsearch_indexed", count=len(chunks), index=self._index_name)

    async def update_fields_by_document(
        self, document_id: uuid.UUID, fields: dict[str, Any]
    ) -> int:
        """Update selected fields on every chunk of one document.

        Re-indexing the whole document body is not an option here: the
        caller reloads chunks from Postgres, which is not the system of
        record for the denormalized `user_id`/`domain`/`tags`/`file_type`/
        `document_name` fields, so a full replace wrote them back as null and
        made the document invisible to its own owner's BM25 filter.
        """
        script_source = (
            "for (e in params.fields.entrySet()) { ctx._source[e.getKey()] = e.getValue(); }"
        )
        try:
            response = await self._client.update_by_query(
                index=self._index_name,
                query={"term": {"document_id": str(document_id)}},
                script={"source": script_source, "params": {"fields": fields}},
                refresh=True,
                conflicts="proceed",
            )
        except Exception as exc:
            logger.warning(
                "elasticsearch_update_failed", document_id=str(document_id), error=str(exc)
            )
            return 0
        updated = int(response.get("updated", 0))
        logger.info(
            "elasticsearch_fields_updated",
            document_id=str(document_id),
            count=updated,
            fields=sorted(fields.keys()),
        )
        return updated

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
            chunk = payload_to_chunk(src, uuid.UUID(str(src.get("chunk_id") or hit["_id"])))
            result.append(BM25ScoredChunk(chunk=chunk, bm25_score=hit["_score"], rank=rank + 1))

        return result

    async def aggregate_facets(
        self,
        fields: list[str],
        filters: BM25SearchFilter | None = None,
        max_values: int = 50,
    ) -> dict[str, list[tuple[str, int]]]:
        """Terms aggregations over keyword fields, inside the access filter.

        `size: 0` because only the buckets are wanted -- returning documents
        alongside them would move megabytes to build a filter sidebar.
        """
        if not fields:
            return {}

        body = {
            "size": 0,
            "query": {"bool": {"filter": self._build_filter_clauses(filters)}},
            "aggs": {field: {"terms": {"field": field, "size": max_values}} for field in fields},
        }
        try:
            response = await self._client.search(index=self._index_name, body=body)
        except Exception as exc:
            # A missing index or an un-aggregatable field should not take the
            # UI down; an empty facet list degrades to "no filters offered".
            logger.warning("elasticsearch_facets_failed", fields=fields, error=str(exc))
            return {}

        aggregations = response.get("aggregations") or {}
        return {
            field: [
                (str(bucket["key"]), int(bucket["doc_count"]))
                for bucket in aggregations.get(field, {}).get("buckets", [])
            ]
            for field in fields
        }

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
        # Reachability: owner OR project member, as one bool/should clause so
        # it narrows as a unit. Two separate filter clauses would mean "owned
        # AND in a project", hiding every document a caller owns outside
        # their projects. Mirrors the Qdrant filter exactly -- if the two
        # backends disagree, hybrid retrieval leaks through whichever is
        # looser.
        if filters.user_id or filters.project_ids:
            reachable: list[dict] = []
            if filters.user_id:
                reachable.append({"term": {"user_id": str(filters.user_id)}})
            if filters.project_ids:
                reachable.append(
                    {"terms": {"project_id": [str(p) for p in filters.project_ids]}}
                )
            clauses.append({"bool": {"should": reachable, "minimum_should_match": 1}})

        if filters.latest_only:
            # Documents indexed before revisions existed have no `is_latest`
            # field and are current by definition; a bare term filter would
            # hide the whole pre-revision corpus.
            clauses.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"is_latest": True}},
                            {"bool": {"must_not": {"exists": {"field": "is_latest"}}}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        if filters.domain:
            clauses.append({"term": {"domain": filters.domain}})
        if filters.tags:
            clauses.append({"terms": {"tags": filters.tags}})
        if filters.file_type:
            clauses.append({"term": {"file_type": filters.file_type}})
        if filters.document_ids:
            clauses.append({"terms": {"document_id": [str(d) for d in filters.document_ids]}})
        if filters.entity_canonicals:
            clauses.append({"terms": {"entity_canonicals": filters.entity_canonicals}})
        if filters.drawing_numbers:
            clauses.append({"terms": {"drawing_number": filters.drawing_numbers}})
        if filters.content_kinds:
            clauses.append({"terms": {"content_kind": filters.content_kinds}})
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
    kwargs: dict[str, Any] = {
        "hosts": [settings.elasticsearch_url],
        "request_timeout": 60,
        # A keep-alive connection that the server has already closed
        # fails the next request with ServerDisconnectedError. Without a
        # retry that surfaces as a 500 on a perfectly healthy cluster,
        # most often after an idle period.
        "max_retries": 3,
        "retry_on_timeout": True,
    }
    if settings.elasticsearch_username:
        kwargs["basic_auth"] = (settings.elasticsearch_username, settings.elasticsearch_password)
    return AsyncElasticsearch(**kwargs)
