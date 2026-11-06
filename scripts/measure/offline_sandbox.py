"""Journey 6 on the packaged app's own Python and code, under a sandbox that denies the
network off loopback. First Offline on, under a profile that kills whatever tries to
leave the Mac; then Offline off, under one that refuses it, as a cut network.
Run: SECUREFOLIO_MODEL_CACHE=... \
  uv run python scripts/measure/offline_sandbox.py <app>"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

# The first kills whatever tries to leave the Mac, so nothing can try and go unnoticed.
KILLS = "(deny network-outbound (with send-signal SIGKILL))"
REFUSES = "(deny network-outbound)"
PROFILE = """(version 1)
(allow default)
{deny}
(allow network-outbound (remote ip "localhost:*"))
(allow network-outbound (subpath "/private/tmp") (subpath "/tmp"))
(allow network-outbound (subpath "/private/var/folders"))
"""
LIBRARY = Path(__file__).parents[2] / "data" / "library"
REPORT = Path(__file__).parents[2] / ".cache" / "offline_sandbox.json"
QUESTION = "When does the car warranty end?"
START_LIMIT = 300
ANSWER_LIMIT = 600


def _ask(client: httpx.Client) -> dict[str, Any]:
    thread = client.post("/threads", json={}).json()["id"]
    begun = time.monotonic()
    run = client.post(f"/threads/{thread}/messages", json={"text": QUESTION}).json()
    while time.monotonic() - begun < ANSWER_LIMIT:
        found = client.get(f"/threads/{thread}").json()
        if found["active_run"] is None:
            answer = found["messages"][-1].get("answer") or {}
            text = "".join(p.get("text", "") for p in answer.get("parts", []))
            return {
                "run": run.get("id") is not None,
                "local": answer.get("local"),
                "fell_back": answer.get("fell_back"),
                "model": answer.get("model"),
                "kind": answer.get("kind"),
                "text": text[:200],
                "seconds": round(time.monotonic() - begun, 1),
            }
        time.sleep(1)
    return {"timed_out": True}


def _start(
    app: Path, vault: Path, profile: Path
) -> tuple[subprocess.Popen, httpx.Client]:
    resources = app / "Contents" / "Resources"
    environment = {
        **os.environ,
        "SECUREFOLIO_VAULT": str(vault),
        "PYTHONPATH": f"{resources / 'code'}",
        "PYTHONNOUSERSITE": "1",
    }
    (vault / "runtime.json").unlink(missing_ok=True)
    process = subprocess.Popen(  # noqa: S603
        [
            "/usr/bin/sandbox-exec",
            "-f",
            str(profile),
            str(resources / "python/bin/python3"),
            str(Path(__file__).with_name("offline_backend.py")),
        ],
        env=environment,
    )
    begun = time.monotonic()
    while not (vault / "runtime.json").exists():
        if time.monotonic() - begun > START_LIMIT:
            raise TimeoutError("the backend didn't start")
        time.sleep(1)
    runtime = json.loads((vault / "runtime.json").read_text())
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{runtime['port']}/api",
        headers={"Authorization": f"Bearer {runtime['app_token']}"},
        timeout=120,
        trust_env=False,
    )
    return process, client


def _wait_for_models_and_queue(client: httpx.Client) -> None:
    begun = time.monotonic()
    while time.monotonic() - begun < ANSWER_LIMIT:
        counts = client.get("/jobs/counts").json()
        models = client.get("/models").json()
        ready = models["search"]["state"] == "installed"
        scanning = any(s["status"] == "scanning" for s in client.get("/sources").json())
        page = models["page"]["state"] == "installed"
        if ready and page and not scanning and counts["active"] == 0:
            return
        time.sleep(5)
    raise TimeoutError("the queue didn't finish")


def _processes(process: subprocess.Popen) -> dict[int, str]:
    parent = psutil.Process(process.pid)
    found = {}
    for member in [parent, *parent.children(recursive=True)]:
        with contextlib.suppress(psutil.Error):
            found[member.pid] = " ".join(member.cmdline())[-80:]
    return found


def _control(app: Path, profile: Path) -> dict[str, int]:
    """That the profile does what it says, with the app's own Python."""
    python = str(app / "Contents" / "Resources" / "python/bin/python3")
    leave = "import socket; socket.create_connection(('1.1.1.1', 443), 2)"
    stay = (
        "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(); "
        "socket.create_connection(s.getsockname(), 2)"
    )
    return {
        name: subprocess.run(  # noqa: S603
            ["/usr/bin/sandbox-exec", "-f", str(profile), python, "-c", code],
            capture_output=True,
            check=False,
        ).returncode
        for name, code in (("leaving_mac", leave), ("loopback", stay))
    }


def _stop(process: subprocess.Popen) -> None:
    process.terminate()
    process.wait(timeout=60)


def main() -> None:
    app = Path(sys.argv[1])
    work = Path(tempfile.mkdtemp(prefix="securefolio-offline-"))
    folder = work / "Home"
    shutil.copytree(LIBRARY / "Home", folder)
    vault = work / "vault"
    vault.mkdir()
    kills = work / "kills.sb"
    kills.write_text(PROFILE.format(deny=KILLS))
    refuses = work / "refuses.sb"
    refuses.write_text(PROFILE.format(deny=REFUSES))
    report: dict[str, Any] = {"control": _control(app, kills)}
    process, client = _start(app, vault, kills)
    try:
        client.post(
            "/sources", json={"path": str(folder), "sensitivity": "standard"}
        ).raise_for_status()
        _wait_for_models_and_queue(client)
        report["offline_switch"] = client.put(
            "/settings/offline", json={"on": True}
        ).status_code
        before = _processes(process)
        report["offline_on"] = _ask(client)
        after = _processes(process)
        report["processes_gone"] = [
            command for pid, command in before.items() if pid not in after
        ]
        report["processes_new"] = [
            command for pid, command in after.items() if pid not in before
        ]
        report["processes"] = len(before)
        client.put("/settings/offline", json={"on": False}).raise_for_status()
    finally:
        _stop(process)
    process, client = _start(app, vault, refuses)
    try:
        report["key_present"] = client.get("/settings/key").json()["present"]
        report["cut_network"] = _ask(client)
        log = client.get("/spending").json()["log"]
        report["cloud_log"] = [
            {"purpose": row["purpose"], "outcome": row["outcome"]} for row in log
        ]
    finally:
        _stop(process)
    REPORT.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
