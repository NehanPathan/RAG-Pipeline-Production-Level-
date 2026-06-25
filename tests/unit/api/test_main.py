from src.api.main import app


def test_app_registers_chat_route():
    paths = {route.path for route in app.routes}
    assert "/api/v1/chat" in paths


def test_app_registers_retrieval_inspect_route():
    paths = {route.path for route in app.routes}
    assert "/api/v1/retrieval/inspect" in paths


def test_app_registers_health_route():
    paths = {route.path for route in app.routes}
    assert "/api/v1/health" in paths
