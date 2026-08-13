from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from src.domain.entities.project import Drawing, Project, ProjectMembership, ProjectRole


class ProjectRepository(ABC):
    @abstractmethod
    async def save(self, project: Project) -> Project: ...

    @abstractmethod
    async def get_by_id(self, project_id: uuid.UUID) -> Project | None: ...

    @abstractmethod
    async def get_by_number(self, project_number: str) -> Project | None: ...

    @abstractmethod
    async def list_for_user(self, user_id: uuid.UUID) -> list[Project]: ...

    @abstractmethod
    async def add_member(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        role: ProjectRole,
        added_by: uuid.UUID | None = None,
    ) -> None: ...

    @abstractmethod
    async def remove_member(self, project_id: uuid.UUID, user_id: uuid.UUID) -> bool: ...

    @abstractmethod
    async def list_members(self, project_id: uuid.UUID) -> list[ProjectMembership]: ...

    @abstractmethod
    async def member_project_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """The projects a user belongs to.

        On the hot path: every retrieval calls this to build the access
        scope, so implementations should treat it as a cached, indexed
        lookup rather than a join.
        """
        ...


class DrawingRepository(ABC):
    @abstractmethod
    async def get_or_create(
        self,
        drawing_number: str,
        project_id: uuid.UUID | None = None,
        sheet_number: str | None = None,
        discipline: str | None = None,
        title: str | None = None,
    ) -> Drawing:
        """Find the drawing this document is a revision of, creating it if new.

        Get-or-create rather than create: the second upload of S-104 must
        attach to the same drawing as the first, or the two revisions become
        unrelated documents and `is_latest` means nothing.
        """
        ...

    @abstractmethod
    async def get_by_id(self, drawing_id: uuid.UUID) -> Drawing | None: ...

    @abstractmethod
    async def list_for_project(self, project_id: uuid.UUID) -> list[Drawing]:
        """The drawing register for one project: one row per drawing.

        Not per document -- `S-104` appears once however many revisions of
        it exist, which is the distinction the drawings table exists for.
        """
        ...

    @abstractmethod
    async def current_revision_id(self, drawing_id: uuid.UUID) -> uuid.UUID | None: ...

    @abstractmethod
    async def revision_document_ids(self, drawing_id: uuid.UUID) -> list[uuid.UUID]:
        """Every document of this drawing, current and superseded.

        Needed for cache invalidation: a new revision must evict cached
        answers derived from *any* earlier one, not merely from the document
        being superseded.
        """
        ...
