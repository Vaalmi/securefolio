"""The gates: tokens, Host, Origin, and what a release build refuses."""

import json
import re
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api_rules import rule_breaks
from backend import auth, build_mode, dependencies, runtime_file, vault
from backend.main import create_app

MCP_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_no_token_gets_a_401(bare_client):
    response = bare_client.get("/api/health")
    assert response.status_code == 401
    assert isinstance(response.json()["detail"], str)


def test_the_app_token_opens_the_app_routes(app_client):
    assert app_client.get("/api/health").json()["database"] == "ready"


def test_the_mcp_token_doesnt_open_an_app_route(bare_client):
    response = bare_client.get(
        "/api/health", headers={"Authorization": "Bearer test-mcp-token"}
    )
    assert response.status_code == 403


def test_the_app_token_doesnt_open_the_mcp_route(app_client):
    response = app_client.post("/mcp", json=MCP_INIT, headers=MCP_HEADERS)
    assert response.status_code == 403
    assert isinstance(response.json()["detail"], str)


def test_no_token_on_the_mcp_route_gets_a_401(bare_client):
    assert (
        bare_client.post("/mcp", json=MCP_INIT, headers=MCP_HEADERS).status_code == 401
    )


def test_the_mcp_token_opens_the_mcp_route(bare_client):
    headers = {"Authorization": "Bearer test-mcp-token", **MCP_HEADERS}
    response = bare_client.post("/mcp", json=MCP_INIT, headers=headers)
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "SecureFolio"


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1:1", "localhost"])
def test_a_wrong_host_is_refused(app_client, host):
    response = app_client.get("/api/health", headers={"Host": host})
    assert response.status_code == 403
    assert "loopback" in response.json()["detail"]


def test_localhost_with_the_port_is_a_right_host(app_client):
    port = app_client.app.state.port
    assert (
        app_client.get("/api/health", headers={"Host": f"localhost:{port}"}).status_code
        == 200
    )


def test_the_apps_origin_is_accepted(app_client):
    headers = {"Origin": dependencies.TAURI_ORIGIN}
    assert app_client.get("/api/health", headers=headers).status_code == 200


def test_another_origin_is_refused(app_client):
    response = app_client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_vite_origins_are_accepted_only_outside_a_release_build(monkeypatch, tmp_path):
    origin = "http://localhost:5199"
    monkeypatch.setenv(build_mode.DEV_ORIGINS_ENV, origin)
    assert dependencies.allowed_origins() == {dependencies.TAURI_ORIGIN, origin}
    marker = tmp_path / "RELEASE"
    marker.touch()
    monkeypatch.setattr(build_mode, "RELEASE_MARKER", marker)
    assert dependencies.allowed_origins() == {dependencies.TAURI_ORIGIN}
    assert build_mode.stub_url(build_mode.STUB_ANTHROPIC_ENV) is None
    assert not build_mode.uses_stub_models()


def test_a_release_build_refuses_a_vite_origin(monkeypatch, tmp_path):
    origin = "http://localhost:5199"
    monkeypatch.setenv(build_mode.DEV_ORIGINS_ENV, origin)
    marker = tmp_path / "RELEASE"
    marker.touch()
    monkeypatch.setattr(build_mode, "RELEASE_MARKER", marker)
    tokens = auth.Tokens("a", "m")
    with TestClient(
        create_app(5, tokens, False), base_url="http://127.0.0.1:5"
    ) as client:
        assert client.get("/api/health", headers={"Origin": origin}).status_code == 403


def test_the_schema_route_is_off(bare_client):
    assert bare_client.get("/openapi.json").status_code == 404


def test_there_are_no_debug_routes(app_client):
    paths = app_client.get("/api/health") and app_client.app.openapi()["paths"]
    assert not [path for path in paths if "debug" in path]
    assert app_client.app.docs_url is None


def test_tokens_differ_and_compare_safely():
    tokens = auth.new_tokens()
    assert tokens.app != tokens.mcp
    assert auth.same(tokens.app, tokens.app)
    assert not auth.same(tokens.app, tokens.mcp)


def test_the_runtime_file_is_private_and_goes_away():
    tokens = auth.new_tokens()
    runtime_file.write(1234, tokens)
    path = vault.runtime_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    written = json.loads(path.read_text())
    assert written["port"] == 1234
    assert written["app_token"] == tokens.app
    assert written["mcp_token"] == tokens.mcp
    assert written["pid"] > 0
    assert written["version"]
    runtime_file.remove()
    assert not path.exists()


def test_every_operation_keeps_the_api_rules(app_client):
    assert rule_breaks(app_client.app.openapi()) == []


def test_the_apps_client_has_one_function_per_operation_it_calls(app_client):
    """api.ts is written by hand, so its names are checked against the API's."""
    source = Path("app/src/api.ts").read_text(encoding="utf-8")
    functions = set(re.findall(r"^export function (\w+)\(", source, re.M))
    operations = {
        operation["operationId"]
        for path in app_client.app.openapi()["paths"].values()
        for operation in path.values()
    }
    camel = {re.sub(r"_(\w)", lambda m: m.group(1).upper(), o) for o in operations}
    # The MCP and setup routes arrive with their own phases' screens.
    assert functions - camel == set()
