from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.entities.project import (
    Drawing,
    Project,
    ProjectMembership,
    ProjectRole,
    ProjectStatus,
)
from src.domain.repositories.project_repository import DrawingRepository, ProjectRepository
from src.domain.value_objects.sensitivity import Sensitivity
from src.infrastructure.database.postgres.models import (
    DocumentModel,
    DrawingModel,
    ProjectMemberModel,
    ProjectModel,
)


class PostgresProjectRepository(ProjectRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, project: Project) -> Project:
        async with self._session_factory() as session:
            session.add(
                ProjectModel(
                    id=project.id,
                    project_number=project.project_number,
                    name=project.name,
                    client_name=project.client_name,
                    status=project.status.value,
                    start_date=project.start_date,
                    target_completion_date=project.target_completion_date,
                    default_sensitivity=(
                        project.default_sensitivity.value if project.default_sensitivity else None
                    ),
                    created_by=project.created_by,
                )
            )
            await session.commit()
        return project

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None:
        async with self._session_factory() as session:
            model = await session.get(ProjectModel, project_id)
            return _to_project(model) if model else None

    async def get_by_number(self, project_number: str) -> Project | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProjectModel).where(ProjectModel.project_number == project_number)
            )
            model = result.scalar_one_or_none()
            return _to_project(model) if model else None

    async def list_for_user(self, user_id: uuid.UUID) -> list[Project]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProjectModel)
                .join(ProjectMemberModel, ProjectMemberModel.project_id == ProjectModel.id)
                .where(ProjectMemberModel.user_id == user_id)
                .order_by(ProjectModel.project_number)
            )
            return [_to_project(m) for m in result.scalars().all()]

    async def add_member(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        role: ProjectRole,
        added_by: uuid.UUID | None = None,
    ) -> None:
        async with self._session_factory() as session:
            # Upsert: adding an existing member with a new role is a role
            # change, not an error. Raising would make the obvious way to
            # promote someone fail.
            await session.execute(
                pg_insert(ProjectMemberModel)
                .values(
                    project_id=project_id,
                    user_id=user_id,
                    project_role=role.value,
                    added_by=added_by,
                )
                .on_conflict_do_update(
                    index_elements=["project_id", "user_id"],
                    set_={"project_role": role.value},
                )
            )
            await session.commit()

    async def remove_member(self, project_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                sa_delete(ProjectMemberModel).where(
                    ProjectMemberModel.project_id == project_id,
                    ProjectMemberModel.user_id == user_id,
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def list_members(self, project_id: uuid.UUID) -> list[ProjectMembership]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProjectMemberModel).where(ProjectMemberModel.project_id == project_id)
            )
            return [
                ProjectMembership(
                    project_id=m.project_id,
                    user_id=m.user_id,
                    project_role=ProjectRole(m.project_role),
                    added_by=m.added_by,
                    added_at=m.added_at,
                )
                for m in result.scalars().all()
            ]

    async def member_project_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProjectMemberModel.project_id).where(ProjectMemberModel.user_id == user_id)
            )
            return list(result.scalars().all())


class PostgresDrawingRepository(DrawingRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create(
        self,
        drawing_number: str,
        project_id: uuid.UUID | None = None,
        sheet_number: str | None = None,
        discipline: str | None = None,
        title: str | None = None,
    ) -> Drawing:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DrawingModel).where(
                    DrawingModel.drawing_number == drawing_number,
                    DrawingModel.project_id == project_id,
                    DrawingModel.sheet_number == sheet_number,
                )
            )
            model = result.scalar_one_or_none()
            if model is not None:
                # Title moves between revisions; the newest wins, because a
                # renamed sheet should show its current name in the register.
                if title and title != model.title:
                    model.title = title
                    await session.commit()
                return _to_drawing(model)

            model = DrawingModel(
                project_id=project_id,
                drawing_number=drawing_number,
                sheet_number=sheet_number,
                discipline=discipline,
                title=title,
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return _to_drawing(model)

    async def get_by_id(self, drawing_id: uuid.UUID) -> Drawing | None:
        async with self._session_factory() as session:
            model = await session.get(DrawingModel, drawing_id)
            return _to_drawing(model) if model else None

    async def list_for_project(self, project_id: uuid.UUID) -> list[Drawing]:
        """The drawing register for one project.

        Ordered by drawing number then sheet, which is how a register is
        read on paper. One row per drawing, not per revision.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(DrawingModel)
                .where(DrawingModel.project_id == project_id)
                .order_by(DrawingModel.drawing_number, DrawingModel.sheet_number)
            )
            return [_to_drawing(model) for model in result.scalars().all()]

    async def current_revision_id(self, drawing_id: uuid.UUID) -> uuid.UUID | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DocumentModel.id).where(
                    DocumentModel.drawing_id == drawing_id,
                    DocumentModel.is_latest.is_(True),
                )
            )
            return result.scalar_one_or_none()

    async def revision_document_ids(self, drawing_id: uuid.UUID) -> list[uuid.UUID]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DocumentModel.id)
                .where(DocumentModel.drawing_id == drawing_id)
                .order_by(DocumentModel.revision_index)
            )
            return list(result.scalars().all())


def _to_project(model: ProjectModel) -> Project:
    return Project(
        id=model.id,
        project_number=model.project_number,
        name=model.name,
        client_name=model.client_name,
        status=ProjectStatus(model.status),
        start_date=model.start_date,
        target_completion_date=model.target_completion_date,
        default_sensitivity=(
            Sensitivity.parse(model.default_sensitivity, Sensitivity.INTERNAL)
            if model.default_sensitivity
            else None
        ),
        created_by=model.created_by,
        created_at=model.created_at,
        updated_at=model.updated_at,
        archived_at=model.archived_at,
    )


def _to_drawing(model: DrawingModel) -> Drawing:
    return Drawing(
        id=model.id,
        project_id=model.project_id,
        drawing_number=model.drawing_number,
        sheet_number=model.sheet_number,
        discipline=model.discipline,
        title=model.title,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )
