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

import time
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


def _wait_for_http(url: str, timeout: float = 180.0, interval: float = 1.0) -> None:
    """Poll an endpoint until it answers.

    Log-line waits are unreliable here: Elasticsearch prints "started" while
    still initialising, so a test that begins immediately afterwards gets
    ServerDisconnectedError. Only a successful HTTP response actually proves
    the service is ready, and it fails the same way whichever container is
    slow that day.
    """
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 500:
                    return
        except Exception as exc:
            last = exc
        time.sleep(interval)
    raise RuntimeError(f"{url} did not become ready within {timeout}s (last error: {last})")


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
    container = DockerContainer("qdrant/qdrant:v1.12.4").with_exposed_ports(6333)
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6333)
        url = f"http://{host}:{port}"
        _wait_for_http(f"{url}/readyz")
        yield url


@pytest.fixture(scope="session")
def elasticsearch_url() -> Iterator[str]:
    from testcontainers.core.container import DockerContainer
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
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9200)
        url = f"http://{host}:{port}"
        # Yellow, not green: a single-node cluster never reaches green
        # because replicas cannot be allocated, and waiting for it times out.
        _wait_for_http(f"{url}/_cluster/health?wait_for_status=yellow&timeout=60s")
        yield url


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

    client = AsyncElasticsearch(
        hosts=[elasticsearch_url],
        request_timeout=30,
        # Same reasoning as the production factory: a stale keep-alive
        # connection must cost a retry, not a failed test.
        max_retries=3,
        retry_on_timeout=True,
    )
    repo = ElasticsearchSearchRepository(
        client=client, index_name=f"chunks_{uuid.uuid4().hex[:8]}"
    )
    await repo.create_index_if_not_exists()
    yield repo
    await client.close()


@pytest.fixture(scope="session")
def mixed_document_pdf(tmp_path_factory):
    """The five-page PDF that is five different kinds of content.

    A real file, because the classifier reads the PDF's own text layer per
    page -- the one signal that cannot be stubbed, since Docling's output
    for a scanned page is indistinguishable from a plotted one.
    """
    pytest.importorskip("matplotlib", reason="the PDF fixtures need matplotlib")
    from tests.pdf_fixtures import build_drawing_pdfs

    return build_drawing_pdfs(tmp_path_factory.mktemp("mixed_pdf"))["mixed"]


@pytest.fixture
async def search_repository(elasticsearch_url: str):
    """A real Elasticsearch index with the application's own mapping.

    Per test with a unique index name: `create_index_if_not_exists`
    short-circuits on an existing index, so a shared one would silently
    serve a stale mapping to whichever test ran second.
    """
    from elasticsearch import AsyncElasticsearch

    from src.infrastructure.search.elasticsearch.repository import (
        ElasticsearchSearchRepository,
    )

    client = AsyncElasticsearch(hosts=[elasticsearch_url])
    index = f"chunks_{uuid.uuid4().hex[:12]}"
    repository = ElasticsearchSearchRepository(client, index)
    await repository.create_index_if_not_exists()
    await repository.ensure_mapping()
    try:
        yield repository
    finally:
        await client.indices.delete(index=index, ignore_unavailable=True)
        await client.close()
