"""Structured search and facets.

Separate from `/chat` on purpose. Chat is a question answered from
documents; this is a filter sidebar over a corpus -- "show me every drawing
in project 2024-0117 that references ISMB 300". A drawing register is
browsed as much as it is asked questions, and routing that through an LLM
would be slower, costlier and less exact than a keyword filter.

Both endpoints apply the caller's access scope and clearance, so a facet
list can never reveal that a project, drawing or part exists that the caller
is not allowed to read.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.api.dependencies import get_project_repository, get_search_repository
from src.api.dependencies_rate_limit import rate_limit
from src.domain.repositories.search_repository import BM25SearchFilter
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.rbac import Principal
from src.monitoring.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

# Fields worth offering as filters. Deliberately not every keyword field:
# `chunk_id` and `parent_chunk_id` are identifiers, not facets, and offering
# them would bury the useful ones.
FACETABLE_FIELDS = (
    "project_number",
    "drawing_number",
    "revision_label",
    "domain",
    "file_type",
    "tags",
    "entity_canonicals",
    "sensitivity",
    # Provenance quality -- "cad_native" vs "scanned_drawing" is the
    # difference between an exact dimension and an OCR reading of one.
    "content_kind",
)


class FacetValue(BaseModel):
    value: str
    count: int


class FacetsResponse(BaseModel):
    facets: dict[str, list[FacetValue]]


class SearchRequest(BaseModel):
    query: str = ""
    project_ids: list[str] = Field(default_factory=list)
    drawing_numbers: list[str] = Field(default_factory=list)
    entity_canonicals: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    file_type: str | None = None
    domain: str | None = None
    # "cad_native" | "vector_drawing" | "scanned_drawing" | "scanned_prose" |
    # "prose". Narrows to a provenance class -- see ContentKind.
    content_kinds: list[str] = Field(default_factory=list)
    # Superseded revisions are excluded unless asked for: answering from an
    # old sheet is worse than not answering.
    include_superseded: bool = False
    top_k: int = 20


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str | None
    page_number: int | None
    section: str | None
    drawing_number: str | None
    revision_label: str | None
    project_number: str | None
    entity_canonicals: list[str]
    content_kind: str | None
    content: str
    score: float


class SearchResponse(BaseModel):
    items: list[SearchHit]
    total: int


async def _access_filter(principal: Principal, include_superseded: bool) -> BM25SearchFilter:
    """The caller's reachability and clearance, as a filter.

    Built here rather than taken from the request so a client cannot widen
    its own scope by sending a different user or project id.
    """
    project_ids: list[uuid.UUID] | None = None
    if principal.user_id is not None:
        try:
            project_ids = await get_project_repository().member_project_ids(principal.user_id)
        except Exception as exc:
            # Fail closed: a degraded lookup narrows reach to owner-only, it
            # never widens it.
            logger.warning("search_project_scope_failed", error=str(exc))
            project_ids = None

    return BM25SearchFilter(
        user_id=principal.user_id,
        project_ids=project_ids,
        sensitivity_in=Sensitivity.values_at_or_below(principal.clearance),
        latest_only=not include_superseded,
    )


@router.get("/search/facets", response_model=FacetsResponse)
async def get_facets(
    fields: list[str] = Query(default=list(FACETABLE_FIELDS)),
    include_superseded: bool = False,
    principal: Principal = Depends(rate_limit("default")),
) -> FacetsResponse:
    """The corpus's actual vocabulary, within the caller's scope.

    What a filter sidebar is built from -- and what stops the query agent
    guessing at values that do not exist.
    """
    wanted = [f for f in fields if f in FACETABLE_FIELDS]
    facets = await get_search_repository().aggregate_facets(
        wanted, filters=await _access_filter(principal, include_superseded)
    )
    return FacetsResponse(
        facets={
            field: [FacetValue(value=value, count=count) for value, count in values]
            for field, values in facets.items()
        }
    )


@router.post("/search", response_model=SearchResponse)
async def structured_search(
    body: SearchRequest,
    principal: Principal = Depends(rate_limit("default")),
) -> SearchResponse:
    """Filter-driven search over chunks.

    An empty `query` is legitimate and common: "everything in this project
    referencing ISMB 300" is a filter, not a question. BM25 needs *some*
    term, so an empty query matches everything and the filters do the work.
    """
    filters = await _access_filter(principal, body.include_superseded)
    if body.project_ids:
        # Narrowed to the requested projects, but only within the ones the
        # caller already belongs to -- intersection, never replacement.
        requested = {uuid.UUID(p) for p in body.project_ids}
        allowed = set(filters.project_ids or ())
        filters.project_ids = sorted(requested & allowed) if allowed else []
    if body.tags:
        filters.tags = body.tags
    if body.file_type:
        filters.file_type = body.file_type
    if body.domain:
        filters.domain = body.domain
    if body.entity_canonicals:
        filters.entity_canonicals = body.entity_canonicals
    if body.drawing_numbers:
        filters.drawing_numbers = body.drawing_numbers
    if body.content_kinds:
        filters.content_kinds = body.content_kinds

    results = await get_search_repository().search(
        body.query or "*", top_k=body.top_k, filters=filters
    )

    return SearchResponse(
        items=[
            SearchHit(
                chunk_id=str(r.chunk.id),
                document_id=str(r.chunk.document_id),
                document_name=r.chunk.document_name,
                page_number=r.chunk.chunk_metadata.page_number,
                section=r.chunk.chunk_metadata.section_title or r.chunk.chunk_metadata.section,
                drawing_number=r.chunk.drawing_number,
                revision_label=r.chunk.revision_label,
                project_number=r.chunk.project_number,
                entity_canonicals=sorted(
                    {
                        str(e["canonical"])
                        for e in r.chunk.chunk_metadata.entities
                        if e.get("canonical")
                    }
                ),
                content_kind=r.chunk.chunk_metadata.content_kind,
                content=r.chunk.content,
                score=r.bm25_score,
            )
            for r in results
        ],
        total=len(results),
    )
