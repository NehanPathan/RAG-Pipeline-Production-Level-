"""Projects, drawings and revision history.

The tables, repositories and `RegisterRevision` all existed; nothing reached
them over HTTP. So a project could be created only by a direct database
write, and a drawing's revision history -- the thing a steel practice
actually navigates by -- could not be read at all.

Every route here enforces membership rather than trusting the request.
Project membership is an access scope, not a label: a caller who is not a
member gets **404, not 403**, matching the convention the documents routes
already use. A 403 confirms the project exists, which is itself a leak when
client names are commercially sensitive.

Membership is checked *in addition to* platform clearance, never instead of
it. A project member still cannot read a document classified above their
clearance -- the two dimensions are independent and both must pass.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field

from src.api.dependencies import (
    get_document_repository,
    get_drawing_repository,
    get_project_repository,
)
from src.api.dependencies_rate_limit import rate_limit
from src.domain.entities.project import Project, ProjectRole, ProjectStatus, revision_sort_index
from src.governance.rbac import Principal
from src.monitoring.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


# --- payloads --------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    project_number: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    client_name: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE


class ProjectResponse(BaseModel):
    """The full project record.

    Every field the client declares is returned, including the ones that are
    usually null. A field the server omits and a field that is genuinely
    empty are indistinguishable to a client, so omitting them would make the
    UI unable to tell "no target date set" from "this server is older than
    that feature".
    """

    id: str
    project_number: str
    name: str
    client_name: str | None
    status: str
    start_date: datetime | None
    target_completion_date: datetime | None
    default_sensitivity: str | None
    created_at: datetime | None
    updated_at: datetime | None
    archived_at: datetime | None
    #: Counters the register view leads with. Computed per request rather
    #: than denormalized: a practice has tens of projects, not millions.
    drawing_count: int | None = None
    member_count: int | None = None
    my_role: str | None = None


class AddMemberRequest(BaseModel):
    user_id: str
    role: ProjectRole = ProjectRole.READER


class MemberResponse(BaseModel):
    project_id: str
    user_id: str
    project_role: str
    added_at: datetime | None
    email: str
    display_name: str | None
    #: The platform role (viewer/analyst/steward/admin), distinct from
    #: `project_role`. Both are shown, because "admin who is a reader here"
    #: and "viewer who owns this project" are both real and both surprising.
    platform_role: str


class DrawingResponse(BaseModel):
    id: str
    drawing_number: str
    sheet_number: str | None
    project_id: str | None
    project_number: str | None
    discipline: str | None
    title: str | None
    created_at: datetime | None
    updated_at: datetime | None
    revision_count: int
    current_revision_label: str | None
    current_document_id: str | None
    current_revision_date: datetime | None
    status: str | None


class RevisionResponse(BaseModel):
    document_id: str
    drawing_id: str
    file_name: str
    revision_label: str | None
    revision_index: int | None
    revision_date: datetime | None
    revision_note: str | None
    is_latest: bool
    superseded_at: datetime | None
    superseded_by_document_id: str | None
    status: str | None
    page_count: int | None
    uploaded_by: str | None
    created_at: datetime | None


# --- access ----------------------------------------------------------------


async def _membership(project_id: uuid.UUID, principal: Principal) -> ProjectRole:
    """The caller's role in a project, or 404 if they have none.

    404 rather than 403 on purpose: a 403 confirms the project exists, and
    for a practice whose client names are commercially sensitive the mere
    existence of a job number is information.
    """
    if principal.user_id is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Project not found")

    for membership in await get_project_repository().list_members(project_id):
        if membership.user_id == principal.user_id:
            return membership.project_role

    raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Project not found")


async def _require_can_write(project_id: uuid.UUID, principal: Principal) -> ProjectRole:
    role = await _membership(project_id, principal)
    if role is ProjectRole.READER:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Read-only membership of this project",
        )
    return role


# --- projects --------------------------------------------------------------


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: CreateProjectRequest,
    principal: Principal = Depends(rate_limit("default")),
) -> ProjectResponse:
    """Create a project and make the caller its owner.

    The creator is added as a member in the same request rather than being
    left to add themselves: a project whose creator cannot see it is the
    kind of state that is only ever noticed by a confused user.
    """
    repository = get_project_repository()
    if await repository.get_by_number(body.project_number):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Project number '{body.project_number}' already exists",
        )

    project = await repository.save(
        Project(
            project_number=body.project_number,
            name=body.name,
            client_name=body.client_name,
            status=body.status,
            created_by=principal.user_id,
        )
    )
    if principal.user_id is not None:
        await repository.add_member(
            project.id, principal.user_id, ProjectRole.OWNER, added_by=principal.user_id
        )

    logger.info(
        "project_created",
        project_id=str(project.id),
        project_number=project.project_number,
        created_by=str(principal.user_id),
    )
    return _project_response(project, ProjectRole.OWNER.value)


@router.get("/projects", response_model=list[ProjectResponse])
async def list_projects(
    principal: Principal = Depends(rate_limit("default")),
) -> list[ProjectResponse]:
    """The caller's own projects. Never a global list."""
    if principal.user_id is None:
        return []
    repository = get_project_repository()
    drawings = get_drawing_repository()
    projects = await repository.list_for_user(principal.user_id)

    out: list[ProjectResponse] = []
    for project in projects:
        members = await repository.list_members(project.id)
        mine = next((m for m in members if m.user_id == principal.user_id), None)
        out.append(
            _project_response(
                project,
                my_role=mine.project_role.value if mine else None,
                drawing_count=len(await drawings.list_for_project(project.id)),
                member_count=len(members),
            )
        )
    return out


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    principal: Principal = Depends(rate_limit("default")),
) -> ProjectResponse:
    role = await _membership(project_id, principal)
    repository = get_project_repository()
    project = await repository.get_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Project not found")
    return _project_response(
        project,
        my_role=role.value,
        drawing_count=len(await get_drawing_repository().list_for_project(project_id)),
        member_count=len(await repository.list_members(project_id)),
    )


# --- members ---------------------------------------------------------------


@router.get("/projects/{project_id}/members", response_model=list[MemberResponse])
async def list_members(
    project_id: uuid.UUID,
    principal: Principal = Depends(rate_limit("default")),
) -> list[MemberResponse]:
    await _membership(project_id, principal)
    return [
        MemberResponse(
            project_id=str(m.project_id),
            user_id=str(m.user_id),
            project_role=m.project_role.value,
            added_at=m.added_at,
            email=m.email,
            display_name=m.display_name,
            platform_role=m.platform_role,
        )
        for m in await get_project_repository().list_member_details(project_id)
    ]


@router.post("/projects/{project_id}/members", status_code=204)
async def add_member(
    project_id: uuid.UUID,
    body: AddMemberRequest,
    principal: Principal = Depends(rate_limit("default")),
) -> None:
    """Grant someone access to a project.

    Owners and contributors may add members; a reader may not. Widening who
    can see a client's drawings is not a read-only operation.
    """
    await _require_can_write(project_id, principal)
    await get_project_repository().add_member(
        project_id, uuid.UUID(body.user_id), body.role, added_by=principal.user_id
    )
    logger.info(
        "project_member_added",
        project_id=str(project_id),
        user_id=body.user_id,
        role=body.role.value,
        added_by=str(principal.user_id),
    )


@router.delete("/projects/{project_id}/members/{user_id}", status_code=204)
async def remove_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(rate_limit("default")),
) -> None:
    await _require_can_write(project_id, principal)
    if not await get_project_repository().remove_member(project_id, user_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Member not found")
    logger.info(
        "project_member_removed",
        project_id=str(project_id),
        user_id=str(user_id),
        removed_by=str(principal.user_id),
    )


# --- drawings and revisions ------------------------------------------------


@router.get("/projects/{project_id}/drawings", response_model=list[DrawingResponse])
async def list_project_drawings(
    project_id: uuid.UUID,
    principal: Principal = Depends(rate_limit("default")),
) -> list[DrawingResponse]:
    """The drawing register for one project.

    One row per *drawing*, not per document: `S-104` appears once with its
    current revision, however many revisions of it have been uploaded. That
    distinction is the whole point of the drawings table.
    """
    await _membership(project_id, principal)
    project = await get_project_repository().get_by_id(project_id)
    drawings = await get_drawing_repository().list_for_project(project_id)
    return [
        await _drawing_response(d, project.project_number if project else None) for d in drawings
    ]


@router.get("/drawings", response_model=list[DrawingResponse])
async def list_drawings(
    project_id: uuid.UUID | None = None,
    search: str | None = None,
    principal: Principal = Depends(rate_limit("default")),
) -> list[DrawingResponse]:
    """Drawings across every project the caller belongs to.

    The register without a project chosen. Scope is built from membership
    rather than taken from the request, so narrowing by `project_id` can only
    ever intersect what the caller already reaches -- asking for a project
    they are not in returns nothing rather than 403, matching the rest of
    this module.
    """
    if principal.user_id is None:
        return []

    repository = get_project_repository()
    projects = await repository.list_for_user(principal.user_id)
    if project_id is not None:
        projects = [p for p in projects if p.id == project_id]

    needle = (search or "").strip().lower()
    results: list[DrawingResponse] = []
    for project in projects:
        for drawing in await get_drawing_repository().list_for_project(project.id):
            if needle and needle not in f"{drawing.drawing_number} {drawing.title or ''}".lower():
                continue
            results.append(await _drawing_response(drawing, project.project_number))
    return results


@router.get("/drawings/{drawing_id}", response_model=DrawingResponse)
async def get_drawing(
    drawing_id: uuid.UUID,
    principal: Principal = Depends(rate_limit("default")),
) -> DrawingResponse:
    drawing = await get_drawing_repository().get_by_id(drawing_id)
    if drawing is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Drawing not found")

    project_number = None
    if drawing.project_id is not None:
        await _membership(drawing.project_id, principal)
        project = await get_project_repository().get_by_id(drawing.project_id)
        project_number = project.project_number if project else None
    return await _drawing_response(drawing, project_number)


@router.get("/drawings/{drawing_id}/revisions", response_model=list[RevisionResponse])
async def list_revisions(
    drawing_id: uuid.UUID,
    principal: Principal = Depends(rate_limit("default")),
) -> list[RevisionResponse]:
    """Every revision of one drawing, newest first.

    Ordered by `revision_index` rather than upload time: drawing sets arrive
    out of sequence often enough -- someone finds Rev B after Rev C is
    already in -- and ordering by arrival would present the register in an
    order that is wrong precisely when it matters.
    """
    drawing = await get_drawing_repository().get_by_id(drawing_id)
    if drawing is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Drawing not found")
    if drawing.project_id is not None:
        await _membership(drawing.project_id, principal)

    documents = []
    for document_id in await get_drawing_repository().revision_document_ids(drawing_id):
        document = await get_document_repository().get_by_id(document_id)
        if document is not None:
            documents.append(document)

    documents.sort(
        key=lambda d: (
            d.revision_index
            if d.revision_index is not None
            else revision_sort_index(d.revision_label or "")
        ),
        reverse=True,
    )
    return [
        RevisionResponse(
            document_id=str(d.id),
            drawing_id=str(drawing_id),
            file_name=d.file_name,
            revision_label=d.revision_label,
            revision_index=d.revision_index,
            revision_date=getattr(d, "revision_date", None),
            revision_note=getattr(d, "revision_note", None),
            is_latest=bool(d.is_latest),
            superseded_at=getattr(d, "superseded_at", None),
            superseded_by_document_id=(
                str(d.superseded_by_document_id) if d.superseded_by_document_id else None
            ),
            status=_status_value(d),
            page_count=getattr(d, "page_count", None),
            uploaded_by=str(d.user_id) if getattr(d, "user_id", None) else None,
            created_at=d.created_at,
        )
        for d in documents
    ]


# --- shaping ---------------------------------------------------------------


def _status_value(document: object) -> str | None:
    status = getattr(document, "status", None)
    return getattr(status, "value", status) if status is not None else None


def _project_response(
    project: Project,
    my_role: str | None = None,
    drawing_count: int | None = None,
    member_count: int | None = None,
) -> ProjectResponse:
    return ProjectResponse(
        id=str(project.id),
        project_number=project.project_number,
        name=project.name,
        client_name=project.client_name,
        status=project.status.value,
        start_date=project.start_date,
        target_completion_date=project.target_completion_date,
        default_sensitivity=(
            project.default_sensitivity.value if project.default_sensitivity else None
        ),
        created_at=project.created_at,
        updated_at=project.updated_at,
        archived_at=project.archived_at,
        drawing_count=drawing_count,
        member_count=member_count,
        my_role=my_role,
    )


async def _drawing_response(drawing, project_number: str | None = None) -> DrawingResponse:
    repository = get_drawing_repository()
    document_ids = await repository.revision_document_ids(drawing.id)
    current_id = await repository.current_revision_id(drawing.id)
    current = await get_document_repository().get_by_id(current_id) if current_id else None
    return DrawingResponse(
        id=str(drawing.id),
        drawing_number=drawing.drawing_number,
        sheet_number=drawing.sheet_number,
        project_id=str(drawing.project_id) if drawing.project_id else None,
        project_number=project_number,
        discipline=drawing.discipline,
        title=drawing.title,
        created_at=drawing.created_at,
        updated_at=drawing.updated_at,
        revision_count=len(document_ids),
        current_revision_label=current.revision_label if current else None,
        current_document_id=str(current_id) if current_id else None,
        current_revision_date=getattr(current, "revision_date", None) if current else None,
        status=_status_value(current) if current else None,
    )
