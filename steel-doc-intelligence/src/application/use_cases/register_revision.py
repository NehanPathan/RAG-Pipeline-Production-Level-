"""Attach an uploaded document to a drawing as its newest revision.

Three things have to happen together, and each has a failure mode that is
quiet rather than loud:

1. **The previous current revision is superseded.** A database partial unique
   index (`uq_drawings_one_latest`) enforces one current revision per
   drawing, so a half-applied supersede fails the write instead of leaving
   two sheets both claiming to be current.

2. **Both search backends learn the flag.** Retrieval filters on `is_latest`
   inside Qdrant and Elasticsearch, before any candidate reaches the
   application. A superseded document whose chunks still say `is_latest` is
   not merely stale metadata -- it is a wrong answer to "what is the current
   detail", delivered confidently.

3. **The semantic cache is invalidated for the whole revision family.** Not
   just the superseded document: a cached answer derived from Rev A is
   equally wrong once Rev C lands, and evicting only the document being
   superseded would leave it in place.

Out-of-order uploads are handled rather than assumed away. Drawing sets
arrive out of sequence often enough -- someone finds Rev B after Rev C is
already in -- and treating "most recently uploaded" as "current" would make
the register wrong in exactly the case a register exists to prevent.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.domain.entities.document import Document
from src.domain.entities.project import Drawing, revision_sort_index
from src.domain.repositories.document_repository import DocumentRepository
from src.domain.repositories.project_repository import DrawingRepository
from src.domain.repositories.search_repository import SearchRepository
from src.domain.repositories.vector_repository import VectorRepository
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

# Injected rather than depending on QueryPipeline: the use case needs one
# function -- "forget cached answers about this document" -- and taking the
# whole pipeline would make the application layer depend on retrieval.
CacheInvalidator = Callable[[uuid.UUID], Awaitable[None]]


@dataclass
class RevisionRegistration:
    drawing: Drawing
    document: Document
    superseded_document_id: uuid.UUID | None
    is_current: bool


class RegisterRevision:
    def __init__(
        self,
        drawing_repo: DrawingRepository,
        document_repo: DocumentRepository,
        vector_repo: VectorRepository,
        search_repo: SearchRepository,
        cache_invalidator: CacheInvalidator | None = None,
    ) -> None:
        self._drawings = drawing_repo
        self._documents = document_repo
        self._vector = vector_repo
        self._search = search_repo
        self._cache_invalidator = cache_invalidator

    async def execute(
        self,
        document: Document,
        drawing_number: str,
        revision_label: str,
        sheet_number: str | None = None,
        discipline: str | None = None,
        title: str | None = None,
        revision_note: str | None = None,
    ) -> RevisionRegistration:
        drawing = await self._drawings.get_or_create(
            drawing_number=drawing_number,
            project_id=document.project_id,
            sheet_number=sheet_number,
            discipline=discipline,
            title=title,
        )

        document.drawing_id = drawing.id
        document.revision_label = revision_label
        document.revision_index = revision_sort_index(revision_label)
        document.revision_note = revision_note

        current_id = await self._drawings.current_revision_id(drawing.id)
        current = await self._documents.get_by_id(current_id) if current_id else None

        # A newly-registered revision is current only if it actually sorts
        # later. Uploading Rev B after Rev C must not demote Rev C.
        is_newer = current is None or document.revision_index > (current.revision_index or -1)
        document.is_latest = is_newer

        superseded_id: uuid.UUID | None = None
        if is_newer and current is not None:
            current.supersede(by_document_id=document.id)
            await self._documents.update(current)
            await self._propagate(current.id, is_latest=False)
            superseded_id = current.id
        elif not is_newer:
            # Recorded as history under the drawing, but not current. The
            # document is still searchable when history is requested.
            document.superseded_by_document_id = current_id

        await self._documents.update(document)
        await self._propagate(document.id, is_latest=document.is_latest)
        await self._invalidate_family(drawing.id)

        logger.info(
            "revision_registered",
            drawing=drawing.label,
            revision=revision_label,
            document_id=str(document.id),
            is_current=document.is_latest,
            superseded=str(superseded_id) if superseded_id else None,
        )
        return RevisionRegistration(
            drawing=drawing,
            document=document,
            superseded_document_id=superseded_id,
            is_current=document.is_latest,
        )

    async def _propagate(self, document_id: uuid.UUID, is_latest: bool) -> None:
        """Push the flag to both search backends.

        A partial payload update, not a re-index: the caller holds no
        embeddings, and re-indexing from Postgres is what previously wiped
        the denormalized tenant fields out of Elasticsearch.
        """
        await self._vector.set_payload_by_document(document_id, {"is_latest": is_latest})
        await self._search.update_fields_by_document(document_id, {"is_latest": is_latest})

    async def _invalidate_family(self, drawing_id: uuid.UUID) -> None:
        """Evict cached answers derived from any revision of this drawing.

        The whole family, because a cached answer built from Rev A is just as
        wrong once Rev C lands as one built from Rev B.
        """
        if self._cache_invalidator is None:
            return
        for document_id in await self._drawings.revision_document_ids(drawing_id):
            await self._cache_invalidator(document_id)
