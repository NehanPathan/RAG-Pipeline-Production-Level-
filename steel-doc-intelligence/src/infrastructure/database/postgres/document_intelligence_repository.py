from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.repositories.document_intelligence_repository import DocumentIntelligenceRepository
from src.domain.value_objects.document_intelligence import (
    DocumentIntelligenceSummary,
    LayoutSummary,
    SimilarityEdge,
)
from src.infrastructure.database.postgres.models import DocumentIntelligenceModel


class PostgresDocumentIntelligenceRepository(DocumentIntelligenceRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, summary: DocumentIntelligenceSummary) -> None:
        layout_outline = {
            "headings": summary.layout.headings,
            "outline": summary.layout.outline,
            "tables_count": summary.layout.tables_count,
            "figures_count": summary.layout.figures_count,
            "lists_count": summary.layout.lists_count,
            "forms_count": summary.layout.forms_count,
            "footnotes_count": summary.layout.footnotes_count,
        }
        semantic_graph = [
            {
                "chunk_id_a": str(edge.chunk_id_a),
                "chunk_id_b": str(edge.chunk_id_b),
                "similarity": edge.similarity,
            }
            for edge in summary.semantic_graph
        ]

        async with self._session_factory() as session:
            # One row per document -- upsert on the unique document_id
            # rather than delete+insert, since re-ingesting the same
            # document_id (a re-index) should replace, not duplicate.
            stmt = pg_insert(DocumentIntelligenceModel).values(
                id=uuid.uuid4(),
                document_id=summary.document_id,
                ocr_engine=summary.ocr_engine,
                ocr_ran=summary.ocr_ran,
                ocr_confidence_avg=summary.ocr_confidence_avg,
                ocr_processing_time_ms=summary.ocr_processing_time_ms,
                ocr_language=summary.ocr_language,
                embedding_model_chunking=summary.embedding_model_chunking,
                embedding_model_retrieval=summary.embedding_model_retrieval,
                layout_outline=layout_outline,
                semantic_graph=semantic_graph,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[DocumentIntelligenceModel.document_id],
                set_={
                    "ocr_engine": stmt.excluded.ocr_engine,
                    "ocr_ran": stmt.excluded.ocr_ran,
                    "ocr_confidence_avg": stmt.excluded.ocr_confidence_avg,
                    "ocr_processing_time_ms": stmt.excluded.ocr_processing_time_ms,
                    "ocr_language": stmt.excluded.ocr_language,
                    "embedding_model_chunking": stmt.excluded.embedding_model_chunking,
                    "embedding_model_retrieval": stmt.excluded.embedding_model_retrieval,
                    "layout_outline": stmt.excluded.layout_outline,
                    "semantic_graph": stmt.excluded.semantic_graph,
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def get_by_document(self, document_id: uuid.UUID) -> DocumentIntelligenceSummary | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DocumentIntelligenceModel).where(
                    DocumentIntelligenceModel.document_id == document_id
                )
            )
            model = result.scalar_one_or_none()
            if model is None:
                return None
            return _to_domain(model)


def _to_domain(model: DocumentIntelligenceModel) -> DocumentIntelligenceSummary:
    outline_data = model.layout_outline or {}
    return DocumentIntelligenceSummary(
        document_id=model.document_id,
        ocr_engine=model.ocr_engine,
        ocr_ran=model.ocr_ran,
        ocr_confidence_avg=model.ocr_confidence_avg,
        ocr_processing_time_ms=model.ocr_processing_time_ms,
        ocr_language=model.ocr_language,
        embedding_model_chunking=model.embedding_model_chunking,
        embedding_model_retrieval=model.embedding_model_retrieval,
        layout=LayoutSummary(
            headings=outline_data.get("headings", []),
            outline=outline_data.get("outline", []),
            tables_count=outline_data.get("tables_count", 0),
            figures_count=outline_data.get("figures_count", 0),
            lists_count=outline_data.get("lists_count", 0),
            forms_count=outline_data.get("forms_count", 0),
            footnotes_count=outline_data.get("footnotes_count", 0),
        ),
        semantic_graph=[
            SimilarityEdge(
                chunk_id_a=uuid.UUID(edge["chunk_id_a"]),
                chunk_id_b=uuid.UUID(edge["chunk_id_b"]),
                similarity=edge["similarity"],
            )
            for edge in (model.semantic_graph or [])
        ],
        created_at=model.created_at,
    )
