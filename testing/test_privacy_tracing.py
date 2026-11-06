"""No SecureFolio process traces to LangSmith, whatever its environment says. A
loopback server stands in for LangSmith's. With tracing asked for and nothing
turning it off, a run's input reaches it (the control); in a process started as
SecureFolio's are, nothing does."""

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

PRIVATE_TEXT = "PRIVATE-LEASE-CLAUSE-7731"
CHILD = "testing.tracing_child"


class _Recorder(BaseHTTPRequestHandler):
    bodies: list[bytes]

    def _record(self) -> None:
        length = int(self.headers.get("content-length", 0))
        self.bodies.append(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    do_POST = do_PATCH = do_GET = _record

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def trace_server() -> Iterator[tuple[list[bytes], int]]:
    bodies: list[bytes] = []
    server = HTTPServer(("127.0.0.1", 0), type("R", (_Recorder,), {"bodies": bodies}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield bodies, server.server_port
    server.shutdown()


def _run_child(port: int, how: str, tmp_path) -> list[str]:
    """Runs the child with an environment asking to trace to the stand-in."""
    env = {
        **os.environ,
        "SECUREFOLIO_MODEL_CACHE": str(tmp_path),
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGSMITH_API_KEY": "lsv2_pt_not_a_real_key",
        "LANGSMITH_ENDPOINT": f"http://127.0.0.1:{port}",
    }
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", CHILD, PRIVATE_TEXT, how],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def _reached(bodies: list[bytes]) -> bool:
    return any(PRIVATE_TEXT.encode() in body for body in bodies)


def test_the_control_traces_a_runs_input_off_the_process(trace_server, tmp_path):
    bodies, port = trace_server
    _run_child(port, "plain", tmp_path)
    assert _reached(bodies)


def test_a_process_started_as_securefolios_traces_nothing(trace_server, tmp_path):
    bodies, port = trace_server
    left = _run_child(port, "securefolio", tmp_path)
    assert bodies == []
    assert left == []  # no LangSmith key or endpoint left for anything to find
