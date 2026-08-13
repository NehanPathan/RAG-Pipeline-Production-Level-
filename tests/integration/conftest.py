"""Real Postgres, Qdrant and Elasticsearch, started per session.

Everything in tests/unit is mocked, which cannot catch the failures that
actually matter in this layer: a Qdrant filter whose shape is subtly wrong,
an Elasticsearch mapping that analyses a field meant to be a keyword, a
migration that does not apply. All three change what a query returns without
changing a single unit test.

The whole directory skips when Docker is unavailable, so `pytest tests/`
still works on a machine without it -- but the skip is loud, because a
silently-skipped integration suite is indistinguishable from a passing one.
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import Iterator

import pytest


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


DOCKER = _docker_available()

_SKIP_REASON = (
    "Docker is not available. Integration tests need real Postgres, Qdrant and "
    "Elasticsearch -- start Docker Desktop and re-run."
)


def pytest_collection_modifyitems(config, items) -> None:
    """Skip everything in this directory when Docker is absent.

    A module-level `pytestmark` in a conftest does not propagate to sibling
    modules, so without this the tests *error* on fixture setup rather than
    skipping -- which reads as a broken suite rather than an unavailable
    dependency.
    """
    if DOCKER:
        return
    skip = pytest.mark.skip(reason=_SKIP_REASON)
    for item in items:
        if "tests/integration" in item.nodeid.replace("\\", "/"):
            item.add_marker(skip)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as container:
        # asyncpg, not psycopg2: the application is async throughout, and the
        # migration runner and repositories share this URL.
        url = container.get_connection_url().replace("postgresql+psycopg2", "postgresql+asyncpg")
        yield url


@pytest.fixture(scope="session")
def qdrant_url() -> Iterator[str]:
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = DockerContainer("qdrant/qdrant:v1.12.4").with_exposed_ports(6333)
    with container:
        wait_for_logs(container, "Actix runtime found", timeout=90)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6333)
        yield f"http://{host}:{port}"


@pytest.fixture(scope="session")
def elasticsearch_url() -> Iterator[str]:
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    # A generic container rather than `ElasticSearchContainer`: that helper
    # is deprecated in testcontainers 4.x and no longer exposes `get_url`,
    # and it does not disable security, which the application's client is not
    # configured for.
    container = (
        DockerContainer("elasticsearch:8.16.1")
        .with_exposed_ports(9200)
        .with_env("discovery.type", "single-node")
        .with_env("xpack.security.enabled", "false")
        # The default heap assumes more memory than a CI runner gives it.
        .with_env("ES_JAVA_OPTS", "-Xms512m -Xmx512m")
    )
    with container:
        wait_for_logs(container, "started", timeout=180)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9200)
        yield f"http://{host}:{port}"


@pytest.fixture
async def migrated_db(postgres_url: str):
    """A Postgres with every migration applied.

    Runs the real Alembic chain rather than `Base.metadata.create_all`, so
    the migrations are themselves under test. The application creates tables
    directly in development, which means a migration can drift from the
    models and nothing notices until a production deploy.
    """
    import asyncio

    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    # env.py builds an async engine from this, so it wants the asyncpg URL.
    config.set_main_option("sqlalchemy.url", postgres_url)

    # Run in a worker thread. `env.py` calls `asyncio.run()` internally, which
    # refuses to nest inside pytest-asyncio's already-running loop; a thread
    # has no loop of its own, so `asyncio.run()` is legal there.
    await asyncio.to_thread(command.upgrade, config, "head")
    yield postgres_url


@pytest.fixture
async def session_factory(migrated_db: str):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(migrated_db)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def qdrant_repo(qdrant_url: str):
    """A vector repository on its own collection.

    Per-test collection names, because a leaked point from one test changes
    another test's top_k and the failure surfaces somewhere unrelated.
    """
    from qdrant_client import AsyncQdrantClient

    from src.infrastructure.vector_store.qdrant.repository import QdrantVectorRepository

    client = AsyncQdrantClient(url=qdrant_url, timeout=30)
    repo = QdrantVectorRepository(client=client, collection_name=f"chunks_{uuid.uuid4().hex[:8]}")
    await repo.create_collection_if_not_exists(vector_size=8)
    yield repo
    await client.close()


@pytest.fixture
async def es_repo(elasticsearch_url: str):
    from elasticsearch import AsyncElasticsearch

    from src.infrastructure.search.elasticsearch.repository import ElasticsearchSearchRepository

    client = AsyncElasticsearch(hosts=[elasticsearch_url], request_timeout=30)
    repo = ElasticsearchSearchRepository(
        client=client, index_name=f"chunks_{uuid.uuid4().hex[:8]}"
    )
    await repo.create_index_if_not_exists()
    yield repo
    await client.close()
