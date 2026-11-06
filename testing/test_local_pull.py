"""A download from Ollama: any tag, one at a time, with progress by tag, every row of
the download table, on a loopback stub."""

import contextlib
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from backend import agent_service, model_store, settings
from backend.build_mode import STUB_OLLAMA_ENV
from testing.stub_models import stub_ollama

RECORDED = json.loads((Path(__file__).parent / "fixtures/ollama_0_40.json").read_text())


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


NEW = "qwen3.5:9b"  # not on this Mac's recorded list until it's pulled


class Ollama:
    """Ollama's side: the recorded list, and a pull that reports progress, then ends as
    told: with success, or with Ollama's own error line."""

    def __init__(self) -> None:
        self.listed = [*RECORDED["tags"]["models"]]
        self.go = threading.Event()
        self.go.set()
        self.error: str | None = None

    def reply(self, path: str, body: dict) -> dict | str:
        if path == "/api/tags":
            return {"models": self.listed}
        if path == "/api/show":
            return RECORDED["show"].get(body["model"], {"capabilities": ["completion"]})
        if path == "/api/ps":
            return {"models": []}
        if path == "/api/pull":
            return self._pull(body["model"])
        return {"model": body.get("model"), "message": {"content": "x"}, "done": True}

    def _pull(self, tag: str) -> str:
        self.go.wait(5)
        if self.error is not None:
            lines = [{"status": "pulling manifest"}, {"error": self.error}]
            return "\n".join(json.dumps(line) for line in lines) + "\n"
        lines = [
            {"status": "pulling manifest"},
            {"status": "pulling", "digest": "sha256:1", "total": 100, "completed": 40},
            {"status": "pulling", "digest": "sha256:1", "total": 100, "completed": 100},
            {"status": "success"},
        ]
        self.listed.append({"name": tag, "model": tag, "size": 100, "digest": "d"})
        return "\n".join(json.dumps(line) for line in lines) + "\n"


@pytest.fixture
def ollama(library, monkeypatch):
    monkeypatch.setattr(agent_service, "OLLAMA_FRESH_SECONDS", 0.0)
    monkeypatch.setattr(model_store, "_capabilities", {}, raising=False)
    monkeypatch.setattr(model_store, "_pull", dict(model_store._pull))  # noqa: SLF001
    side = Ollama()
    with stub_ollama() as stub:
        stub.reply = side.reply
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        stub.side = side
        yield stub
    side.go.set()


def pull(app_client, tag: str = NEW):
    return app_client.post("/api/models/local/pull", json={"tag": tag})


def settled(app_client) -> dict:
    for _ in range(200):
        found = app_client.get("/api/models").json()["local"]["pull"]
        if found["state"] != model_store.DOWNLOADING:
            return found
        time.sleep(0.02)
    raise AssertionError("the download never ended")


class TestTheDownload:
    def test_any_tag_is_pulled_and_joins_the_list(self, ollama, app_client):
        assert pull(app_client).status_code == 200
        found = settled(app_client)
        assert (found["tag"], found["state"]) == (NEW, model_store.INSTALLED)
        assert (found["done"], found["total"]) == (100, 100)
        names = {
            m["name"]
            for m in app_client.get("/api/models").json()["installed"]["local"]
        }
        assert NEW in names, "it joins the list it now suits"
        (sent,) = [r for r in ollama.requests if r.path == "/api/pull"]
        assert sent.body["model"] == NEW

    def test_its_progress_is_kept_by_tag(self, ollama, app_client):
        ollama.side.go.clear()
        assert pull(app_client).status_code == 200
        going = app_client.get("/api/models").json()["local"]["pull"]
        assert (going["tag"], going["state"]) == (NEW, model_store.DOWNLOADING)
        ollama.side.go.set()
        assert settled(app_client)["state"] == model_store.INSTALLED

    def test_no_such_model_fails_with_ollamas_reason_in_plain_words(
        self, ollama, app_client
    ):
        ollama.side.error = "pull model manifest: file does not exist"
        assert pull(app_client, "nosuch:1b").status_code == 200
        found = settled(app_client)
        assert found["state"] == model_store.FAILED
        assert found["reason"] == (
            "Ollama has no model called nosuch:1b. Check the name on ollama.com."
        )

    def test_after_a_failure_or_an_install_a_new_pull_starts(self, ollama, app_client):
        ollama.side.error = "pull model manifest: file does not exist"
        pull(app_client, "nosuch:1b")
        assert settled(app_client)["state"] == model_store.FAILED
        ollama.side.error = None
        assert pull(app_client).status_code == 200
        assert settled(app_client)["state"] == model_store.INSTALLED
        assert pull(app_client, "llama3.2:3b").status_code == 200
        assert settled(app_client)["tag"] == "llama3.2:3b"


class TestRefused:
    def test_in_offline_mode(self, ollama, app_client):
        settings.put(settings.OFFLINE, True)
        refused = pull(app_client)
        assert refused.status_code == 409
        assert [r for r in ollama.requests if r.path == "/api/pull"] == []

    @pytest.mark.parametrize("tag", ["gpt-oss:120b-cloud", "deepseek-v3:cloud"])
    def test_for_a_tag_on_ollamas_servers(self, ollama, app_client, tag):
        refused = pull(app_client, tag)
        assert refused.status_code == 422
        assert "Ollama's servers" in refused.json()["detail"]

    def test_for_a_cloud_model(self, ollama, app_client):
        refused = pull(app_client, "anthropic:claude-haiku-5-5")
        assert refused.status_code == 422

    def test_while_another_runs(self, ollama, app_client):
        ollama.side.go.clear()
        assert pull(app_client).status_code == 200
        refused = pull(app_client, "llama3.2:3b")
        assert refused.status_code == 409
        assert "Another model is downloading" in refused.json()["detail"]
        ollama.side.go.set()
        settled(app_client)

    def test_with_ollama_stopped(self, library, app_client, monkeypatch):
        monkeypatch.setattr(model_store, "_pull", dict(model_store._pull))  # noqa: SLF001
        monkeypatch.setenv(STUB_OLLAMA_ENV, f"http://127.0.0.1:{free_port()}")
        refused = pull(app_client)
        assert refused.status_code == 409
        assert "Ollama isn't running" in refused.json()["detail"]


class TestTwoAtOnce:
    def test_two_pulls_asked_for_together_start_one(self, ollama, monkeypatch):
        # Both checks pass before either claims the slot, unless they're one.
        ollama.side.go.clear()
        real = model_store._refuse_a_pull  # noqa: SLF001
        both_checked = threading.Barrier(2, timeout=0.5)

        def slow_check(tag, pull):
            real(tag, pull)
            # The second check comes before the first claim; under the lock, the other
            # never gets here first, and the wait gives up.
            with contextlib.suppress(threading.BrokenBarrierError):
                both_checked.wait()

        monkeypatch.setattr(model_store, "_refuse_a_pull", slow_check)
        outcomes = []

        def start(tag: str) -> None:
            try:
                model_store.start_local_pull(tag, agent_service.local_pull())
                outcomes.append("started")
            except model_store.refusals.Refused:
                outcomes.append("refused")

        threads = [
            threading.Thread(target=start, args=(t,)) for t in (NEW, "llama3.2:3b")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(outcomes) == ["refused", "started"]
        ollama.side.go.set()
