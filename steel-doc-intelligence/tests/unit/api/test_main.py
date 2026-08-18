from src.api.main import app

# Read the paths from the OpenAPI schema rather than by walking `app.routes`.
# FastAPI 0.138 stopped flattening included routers into `app.routes` -- the
# top level now holds `_IncludedRouter` wrappers that carry no `.path`, and
# reconstructing the prefix from them means reaching into private attributes
# that can change again. The generated schema is the public, fully-prefixed
# answer to the only question these tests ask: does the app serve this path.
PATHS = set(app.openapi()["paths"])


def test_app_registers_chat_route():
    assert "/api/v1/chat" in PATHS


def test_app_registers_retrieval_inspect_route():
    assert "/api/v1/retrieval/inspect" in PATHS


def test_app_registers_health_route():
    assert "/api/v1/health" in PATHS
