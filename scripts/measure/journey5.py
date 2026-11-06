"""Journey 5: with no window open, a stdio client walks the calls Claude makes, through
the bridge, against the packaged app. The app starts itself if it isn't running.
Run: SECUREFOLIO_MODEL_CACHE=... uv run python scripts/measure/journey5.py <app>"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

LIBRARY = Path(__file__).parents[2] / "data" / "library"
REPORT = Path(__file__).parents[2] / ".cache" / "journey5.json"
START_LIMIT = 300
QUEUE_LIMIT = 900


class Bridge:
    def __init__(self, binary: Path, vault: Path) -> None:
        self.process = subprocess.Popen(  # noqa: S603
            [str(binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "SECUREFOLIO_VAULT": str(vault),
                "SECUREFOLIO_NO_LAUNCH": "1",
            },
        )
        self.next_id = 0

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        message = {
            "jsonrpc": "2.0",
            "id": self.next_id,
            "method": method,
            "params": params,
        }
        stdin, stdout = self.process.stdin, self.process.stdout
        if stdin is None or stdout is None:
            raise RuntimeError
        stdin.write(json.dumps(message) + "\n")
        stdin.flush()
        return json.loads(stdout.readline())

    def tool(self, name: str, **arguments: Any) -> Any:
        found = self.call("tools/call", {"name": name, "arguments": arguments})
        result = found["result"]
        if result.get("isError"):
            return {"error": result["content"][0]["text"]}
        structured = result.get("structuredContent")
        return structured.get("result", structured) if structured else result["content"]


def main() -> None:
    app = Path(sys.argv[1])
    work = Path(tempfile.mkdtemp(prefix="securefolio-journey5-"))
    vault = work / "vault"
    vault.mkdir()
    for name in ("Home", "Taxes"):
        shutil.copytree(LIBRARY / name, work / name)
    shell = subprocess.Popen(  # noqa: S603
        [str(app / "Contents/MacOS/securefolio"), "--headless"],
        env={**os.environ, "SECUREFOLIO_VAULT": str(vault)},
        start_new_session=True,
    )
    report: dict[str, Any] = {}
    try:
        begun = time.monotonic()
        while not (vault / "runtime.json").exists():
            if time.monotonic() - begun > START_LIMIT:
                raise TimeoutError
            time.sleep(1)
        runtime = json.loads((vault / "runtime.json").read_text())
        client = httpx.Client(
            base_url=f"http://127.0.0.1:{runtime['port']}/api",
            headers={"Authorization": f"Bearer {runtime['app_token']}"},
            timeout=60,
            trust_env=False,
        )
        for name, level, mcp in (
            ("Home", "standard", True),
            ("Taxes", "private", False),
        ):
            client.post(
                "/sources",
                json={
                    "path": str(work / name),
                    "sensitivity": level,
                    "mcp_enabled": mcp,
                },
            ).raise_for_status()
        begun = time.monotonic()
        while time.monotonic() - begun < QUEUE_LIMIT:
            time.sleep(5)
            live = client.get("/sources").json()
            active = client.get("/jobs/counts").json()["active"]
            if active == 0 and all(s["status"] != "scanning" for s in live):
                break
        bridge = Bridge(app / "Contents/Resources/securefolio-mcp", vault)
        report["initialize"] = bridge.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "journey5", "version": "1"},
            },
        )["result"]["serverInfo"]["name"]
        report["tools"] = [
            tool["name"] for tool in bridge.call("tools/list", {})["result"]["tools"]
        ]
        hits = bridge.tool(
            "search_library", query="What does my lease say about pets?", limit=5
        )
        report["top_hit"] = {k: hits[0][k] for k in ("title", "uri", "where")}
        report["pets_found"] = any("cat" in hit["text"] for hit in hits)
        report["passage"] = bridge.tool("read_passage", uri=hits[0]["uri"])["text"][
            :160
        ]
        report["document_link"] = bridge.tool(
            "get_document", document_id=hits[0]["document_id"]
        )["link"]
        report["collections"] = bridge.tool("list_collections")
        tax = bridge.tool("search_library", query="federal return refund", limit=10)
        report["private_titles_seen"] = [
            hit["title"] for hit in tax if "return" in hit["title"].casefold()
        ]
        bridge.process.kill()
        REPORT.write_text(json.dumps(report, indent=1))
    finally:
        for member in psutil.Process(shell.pid).children(recursive=True):
            with contextlib.suppress(psutil.Error):
                member.terminate()
        with contextlib.suppress(OSError):  # the family going may have taken it
            os.killpg(shell.pid, signal.SIGTERM)
        shell.wait(timeout=60)


if __name__ == "__main__":
    main()
