"""The stdio bridge Claude Desktop and Claude Code run: with the backend down, it waits,
and answers once the backend is up. It reads runtime.json fresh."""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from backend import auth, vault
from backend.main import NO_WORKER_ENV, create_app, free_port

CRATE = Path(__file__).parents[1] / "desktop" / "mcp-bridge"
BINARY = CRATE / "target" / "debug" / "securefolio-mcp"
WAIT_SECONDS = 60


@pytest.fixture(scope="module")
def binary() -> Path:
    subprocess.run(  # noqa: S603
        ["cargo", "build", "--offline", "--manifest-path", str(CRATE / "Cargo.toml")],  # noqa: S607
        check=True,
        capture_output=True,
    )
    return BINARY


def _start_backend(monkeypatch) -> uvicorn.Server:
    monkeypatch.setenv(NO_WORKER_ENV, "1")
    port = free_port()
    app = create_app(port, auth.new_tokens())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def test_with_the_backend_down_then_up(library, binary, monkeypatch):
    bridge = subprocess.Popen(  # noqa: S603
        [str(binary)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "SECUREFOLIO_VAULT": str(vault.root()),
            "SECUREFOLIO_NO_LAUNCH": "1",
        },
    )
    assert bridge.stdin
    assert bridge.stdout
    server = None
    try:
        request = {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}}
        bridge.stdin.write(json.dumps(request) + "\n")
        bridge.stdin.flush()
        time.sleep(1)
        assert bridge.poll() is None  # waiting for the backend, not failing
        server = _start_backend(monkeypatch)
        line = bridge.stdout.readline()
        answer = json.loads(line)
        assert answer["id"] == 7
        names = {tool["name"] for tool in answer["result"]["tools"]}
        assert names == {
            "search_library",
            "read_passage",
            "get_document",
            "list_collections",
        }
    finally:
        bridge.kill()
        bridge.wait(WAIT_SECONDS)
        if server:
            server.should_exit = True


def test_the_extension_runs_the_bridge_as_a_binary():
    manifest = json.loads((CRATE / "manifest.json").read_text())
    assert manifest["server"]["type"] == "binary"
    assert {tool["name"] for tool in manifest["tools"]} == {
        "search_library",
        "read_passage",
        "get_document",
        "list_collections",
    }
