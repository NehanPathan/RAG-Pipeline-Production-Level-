"""One definition of how a DocumentChunk is written to a search backend.

Qdrant and Elasticsearch each used to hand-build a near-identical payload
dict and each used to reconstruct a chunk from it by hand. The two copies
drifted: `section_title` and `heading_level` were persisted to Postgres and
returned by `/documents/{id}/chunks`, but neither payload builder wrote
them, so every chunk that came back from retrieval had them as `None` and
citations lost their section on the Docling path.

Both repositories now go through the pair below, so a field added here
lands in both stores by construction rather than by remembering to edit two
files. The steel-domain work adds several such fields (regions, entity
canonicals, drawing/revision identity), which is why this is worth fixing
before any of them are introduced.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.domain.value_objects.provenance import Region
from src.domain.value_objects.sensitivity import Sensitivity


def chunk_to_payload(chunk: DocumentChunk) -> dict[str, Any]:
    """Flatten a chunk into the payload/document body both backends store."""
    return {
        "chunk_id": str(chunk.id),
        "document_id": str(chunk.document_id),
        "user_id": str(chunk.user_id) if chunk.user_id else None,
        "content": chunk.content,
        "chunk_type": chunk.chunk_type.value,
        "position": chunk.position,
        "token_count": chunk.token_count,
        "page_number": chunk.chunk_metadata.page_number,
        "section": chunk.chunk_metadata.section,
        "section_title": chunk.chunk_metadata.section_title,
        "heading_level": chunk.chunk_metadata.heading_level,
        "contains_table": chunk.chunk_metadata.contains_table,
        "parent_chunk_id": str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
        "domain": chunk.domain,
        "tags": chunk.tags,
        "file_type": chunk.file_type,
        "document_name": chunk.document_name,
        "sensitivity": chunk.sensitivity.value,
        "project_id": str(chunk.project_id) if chunk.project_id else None,
        "project_number": chunk.project_number,
        "drawing_id": str(chunk.drawing_id) if chunk.drawing_id else None,
        "drawing_number": chunk.drawing_number,
        "revision_label": chunk.revision_label,
        "is_latest": chunk.is_latest,
        # Canonical forms only, as a flat keyword list. The full entity
        # records (provenance, attributes, confidence) stay in Postgres --
        # what a search backend needs is something exactly matchable, and
        # indexing nested objects per chunk would cost far more than it
        # returns.
        "entity_canonicals": _entity_canonicals(chunk),
        # Provenance quality: filterable so "only chunks read from CAD" is a
        # query rather than a guess.
        "content_kind": chunk.chunk_metadata.content_kind,
        # Where on the page. Stored, never indexed or filtered on -- these are
        # coordinates for a viewer to draw, and indexing thousands of float
        # subfields per document would cost a great deal and return nothing.
        "regions": [region.to_dict() for region in chunk.chunk_metadata.regions],
        "region_precision": chunk.chunk_metadata.region_precision,
        # Indexed keyword list. A drawing question is structural far more
        # often than semantic -- "what is on S-BOLTS" wants a filter.
        "layers": sorted(set(chunk.chunk_metadata.layers)),
    }


def _entity_canonicals(chunk: DocumentChunk) -> list[str]:
    return sorted(
        {
            str(entity["canonical"])
            for entity in chunk.chunk_metadata.entities
            if entity.get("canonical")
        }
    )


def payload_to_chunk(payload: Mapping[str, Any], chunk_id: uuid.UUID) -> DocumentChunk:
    """Rebuild a chunk from a stored payload.

    `chunk_id` is passed separately because Qdrant carries it as the point id
    and Elasticsearch carries it in the document body; the payload's own
    `chunk_id` key is not relied on to be present.
    """
    return DocumentChunk(
        id=chunk_id,
        document_id=uuid.UUID(str(payload["document_id"])),
        content=payload.get("content", ""),
        position=payload.get("position", 0),
        chunk_type=ChunkType(payload.get("chunk_type", "child")),
        token_count=payload.get("token_count", 0),
        parent_chunk_id=_optional_uuid(payload.get("parent_chunk_id")),
        chunk_metadata=ChunkMetadata(
            page_number=payload.get("page_number"),
            section=payload.get("section"),
            section_title=payload.get("section_title"),
            heading_level=payload.get("heading_level"),
            contains_table=payload.get("contains_table", False),
            # Canonical forms only. Provenance, attributes and confidence
            # live in Postgres and are not worth carrying in a search
            # payload -- but a retrieved chunk that could not say which
            # designations it contains would make the entity filter's own
            # results unexplainable.
            entities=[
                {"canonical": canonical} for canonical in (payload.get("entity_canonicals") or [])
            ],
            content_kind=payload.get("content_kind"),
            # A malformed region costs a highlight, not an answer, so
            # `from_dict` returns None rather than raising and those are
            # dropped here.
            regions=[
                region
                for region in (Region.from_dict(raw) for raw in (payload.get("regions") or []))
                if region is not None
            ],
            region_precision=payload.get("region_precision"),
            layers=list(payload.get("layers") or []),
        ),
        user_id=_optional_uuid(payload.get("user_id")),
        domain=payload.get("domain"),
        tags=payload.get("tags") or [],
        file_type=payload.get("file_type"),
        document_name=payload.get("document_name"),
        project_id=_optional_uuid(payload.get("project_id")),
        project_number=payload.get("project_number"),
        drawing_id=_optional_uuid(payload.get("drawing_id")),
        drawing_number=payload.get("drawing_number"),
        revision_label=payload.get("revision_label"),
        # Rows written before revisions existed have no flag and are current
        # by definition: nothing supersedes them.
        is_latest=bool(payload.get("is_latest", True)),
        # Rows written before this field existed have no `sensitivity` key;
        # Sensitivity.parse resolves those to INTERNAL rather than PUBLIC so
        # legacy data fails closed.
        sensitivity=Sensitivity.parse(payload.get("sensitivity"), Sensitivity.INTERNAL),
    )


def _optional_uuid(value: Any) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None
