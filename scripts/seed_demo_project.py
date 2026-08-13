"""Ingest sample drawings into a demo project, and grant access to it.

Two jobs, both idempotent, because they happen at different times:

1. **Ingest.** Runs once; re-running skips documents already indexed.
2. **Grant.** Adds every user currently in the database to the project as a
   contributor. This has to be separate because a Firebase user's local id is
   derived from their Firebase uid, so their row does not exist until they
   have signed in once. Re-run this after logging in and the drawings appear.

Usage, from inside the api container (it already holds the embedding models):

    python -m scripts.seed_demo_project              # ingest + grant
    python -m scripts.seed_demo_project --grant-only # after a first login

This is a demonstration aid, not a fixture the product depends on. The
frontend ships with mocks off precisely so a deployed container cannot look
like a working system full of drawings that do not exist; seeded data is
real data, ingested through the real pipeline, and it is obvious in the UI
which project it belongs to.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from sqlalchemy import select

from src.api.dependencies import get_document_repository, get_ingestion_pipeline
from src.domain.entities.document import Document
from src.domain.entities.project import Project, ProjectRole
from src.domain.value_objects.sensitivity import Sensitivity
from src.infrastructure.database.postgres.connection import get_session_factory
from src.infrastructure.database.postgres.models import UserModel
from src.infrastructure.database.postgres.project_repository import (
    PostgresProjectRepository,
)

PROJECT_NUMBER = "2024-0117"
PROJECT_NAME = "Warehouse Extension — Demo"
CLIENT_NAME = "Acme Steel Fabrication"
SAMPLES = Path("/app/data/samples")


async def _ensure_project(repository: PostgresProjectRepository) -> Project:
    existing = await repository.get_by_number(PROJECT_NUMBER)
    if existing is not None:
        print(f"  project {PROJECT_NUMBER} already exists")
        return existing
    project = await repository.save(
        Project(
            project_number=PROJECT_NUMBER,
            name=PROJECT_NAME,
            client_name=CLIENT_NAME,
            default_sensitivity=Sensitivity.INTERNAL,
        )
    )
    print(f"  created project {PROJECT_NUMBER}")
    return project


async def _grant_everyone(repository: PostgresProjectRepository, project: Project) -> int:
    """Add every existing user to the project.

    Deliberately everyone rather than a named email: this runs on a developer
    machine to make a demo visible, and guessing which of several accounts is
    "the" user is exactly the kind of cleverness that then needs explaining.
    """
    factory = get_session_factory()
    async with factory() as session:
        users = (await session.execute(select(UserModel))).scalars().all()

    existing = {m.user_id for m in await repository.list_members(project.id)}
    added = 0
    for user in users:
        if user.id in existing:
            continue
        await repository.add_member(project.id, user.id, ProjectRole.CONTRIBUTOR)
        print(f"  granted {user.email} contributor on {PROJECT_NUMBER}")
        added += 1
    if not added:
        print(f"  every known user ({len(users)}) already has access")
    return added


async def _ingest(project: Project, owner_id: uuid.UUID) -> int:
    files = sorted(p for p in SAMPLES.glob("*") if p.suffix.lower() in {".pdf", ".dxf"})
    if not files:
        print(f"  no sample files under {SAMPLES}")
        return 0

    repository = get_document_repository()
    pipeline = get_ingestion_pipeline()
    indexed = 0

    for path in files:
        existing, _ = await repository.list_by_user(owner_id, size=200)
        if any(d.file_name == path.name for d in existing):
            print(f"  {path.name}: already ingested, skipping")
            continue

        document = Document(
            file_name=path.name,
            file_type=path.suffix.lstrip(".").lower(),
            file_size_bytes=path.stat().st_size,
            user_id=owner_id,
            project_id=project.id,
            sensitivity=Sensitivity.INTERNAL,
        )
        document.file_path = str(path)
        await repository.save(document)

        print(f"  {path.name}: ingesting ...", flush=True)
        result = await pipeline.ingest(document, path)
        print(f"  {path.name}: {result.status.value}, {result.chunks_created} chunks")
        indexed += 1

    return indexed


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grant-only",
        action="store_true",
        help="skip ingestion; only grant project access to existing users",
    )
    args = parser.parse_args()

    repository = PostgresProjectRepository(get_session_factory())

    print("\nProject")
    project = await _ensure_project(repository)

    if not args.grant_only:
        factory = get_session_factory()
        async with factory() as session:
            owner = (await session.execute(select(UserModel))).scalars().first()
        if owner is None:
            print("\n  no users exist yet -- sign in once, then re-run")
            return
        print("\nIngestion")
        await _ingest(project, owner.id)

    print("\nAccess")
    await _grant_everyone(repository, project)
    print(
        f"\nDone. Sign in, open the project register, and look for {PROJECT_NUMBER}."
        "\nAfter a first sign-in on a new account, re-run with --grant-only.\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
