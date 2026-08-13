"""Projects and drawings.

A project is an access scope, not a label. It is the unit engineers share
with one another, and making it a scope is what lets two people on the same
job see each other's drawings without making every document global.

A drawing is an identity that outlives its revisions. `S-104` is one
drawing; Rev A, Rev B and Rev C are three documents of it. Without that
distinction "the current baseplate thickness on S-104" has no referent.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from src.domain.value_objects.sensitivity import Sensitivity


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    CLOSED = "closed"
    ARCHIVED = "archived"


class ProjectRole(str, Enum):
    """A member's standing within one project.

    Distinct from the platform `Role` in governance/rbac.py, which decides
    what someone may do at all. This decides which projects they may do it
    in. Both must pass.
    """

    OWNER = "owner"
    CONTRIBUTOR = "contributor"
    READER = "reader"


@dataclass
class Project:
    project_number: str
    name: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    client_name: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    start_date: datetime | None = None
    target_completion_date: datetime | None = None
    default_sensitivity: Sensitivity | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    archived_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status is ProjectStatus.ACTIVE and self.archived_at is None


@dataclass(frozen=True)
class ProjectMembership:
    project_id: uuid.UUID
    user_id: uuid.UUID
    project_role: ProjectRole = ProjectRole.READER
    added_by: uuid.UUID | None = None
    added_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def can_upload(self) -> bool:
        return self.project_role in (ProjectRole.OWNER, ProjectRole.CONTRIBUTOR)


@dataclass(frozen=True)
class ProjectMemberDetail:
    """A membership joined to who the member actually is.

    `ProjectMembership` carries only a user id, which is all the access
    checks need and all they should depend on. A member *list*, though, is
    read by a person deciding whether to remove someone, and a page of bare
    UUIDs cannot support that decision.
    """

    project_id: uuid.UUID
    user_id: uuid.UUID
    project_role: ProjectRole
    added_at: datetime
    email: str
    display_name: str | None = None
    platform_role: str = "viewer"


@dataclass
class Drawing:
    drawing_number: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    project_id: uuid.UUID | None = None
    sheet_number: str | None = None
    discipline: str | None = None
    title: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def label(self) -> str:
        return (
            f"{self.drawing_number}/{self.sheet_number}"
            if self.sheet_number
            else self.drawing_number
        )


# Revision labels are not consistent across practices. Alphabetic (A, B, C),
# numeric (0, 1, 2), and prefixed schemes (P1 preliminary, C1 construction)
# all occur, sometimes within one project. Sorting the label text would put
# "B" after "10" and "C1" before "P1", so ordering uses an integer derived
# once at registration and stored.
_ALPHA = re.compile(r"^([A-Z])$")
_NUMERIC = re.compile(r"^(\d{1,3})$")
_PREFIXED = re.compile(r"^([A-Z]{1,2})\s*-?\s*(\d{1,3})$")

# Prefixed schemes carry a stage, and a construction issue supersedes a
# preliminary one regardless of its number. The offset keeps the stages from
# interleaving.
_STAGE_OFFSET = {"P": 0, "T": 100, "C": 200, "A": 300, "F": 400}


def revision_sort_index(label: str) -> int:
    """Map a revision label to a sortable integer.

    Unrecognised labels sort last rather than first: a scheme we do not
    understand is more likely to be a recent addition than an early draft,
    and putting it first would silently mark a genuinely current sheet as
    superseded.
    """
    text = (label or "").strip().upper()
    if not text:
        return 0

    if match := _NUMERIC.match(text):
        return int(match.group(1))
    if match := _ALPHA.match(text):
        return ord(match.group(1)) - ord("A") + 1
    if match := _PREFIXED.match(text):
        prefix, number = match.group(1), int(match.group(2))
        return _STAGE_OFFSET.get(prefix[0], 500) + number
    return 9_999
