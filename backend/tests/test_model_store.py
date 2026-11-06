"""The model download: progress, resuming after a kill, verification, Offline."""

import hashlib
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend import build_mode, model_store, refusals, settings, vault
from worker import pacing

BODY = b"model weights " * 1000
REPO = "Org/Model"


def manifest(sha: str | None = None) -> list[dict]:
    return [
        {
            "repo": REPO,
            "group": "search",
            "revision": "abc123",
            "refs": {"main": "abc123", "v1.0": "abc123"},
            "files": [
                {
                    "path": "sub/weights.bin",
                    "size": len(BODY),
                    "sha256": sha or hashlib.sha256(BODY).hexdigest(),
                }
            ],
        }
    ]


class Server:
    def __init__(self) -> None:
        self.ranges: list[str | None] = []
        self.fail_after: int | None = None
        self.refuse_ranges = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                wanted = self.headers.get("Range")
                outer.ranges.append(wanted)
                if wanted and outer.refuse_ranges:
                    outer.refuse_ranges = False
                    self.send_response(416)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = int(wanted[6:-1]) if wanted else 0
                self.send_response(206 if wanted else 200)
                self.send_header("Content-Length", str(len(BODY) - start))
                self.end_headers()
                sent = BODY[start:]
                if outer.fail_after is not None:
                    sent = sent[: outer.fail_after]
                self.wfile.write(sent)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()


@pytest.fixture
def store(library, monkeypatch, tmp_path):
    server = Server()
    monkeypatch.setenv(model_store.STUB_MODELS_ENV, server.url)
    monkeypatch.setenv("SECUREFOLIO_MODEL_CACHE", str(tmp_path / "models"))
    monkeypatch.setattr(build_mode, "uses_stub_models", lambda: False)
    monkeypatch.setattr(model_store, "manifest", manifest)
    monkeypatch.setattr(model_store, "_launch", lambda: None)
    yield server
    server.http.shutdown()


def state() -> dict:
    return model_store.status()[0]


def target(tmp_path):
    return (
        tmp_path
        / "models/huggingface/hub/models--Org--Model/snapshots/abc123/sub/weights.bin"
    )


def test_it_downloads_verifies_and_writes_what_hugging_face_reads(store, tmp_path):
    assert not model_store.search_ready()
    model_store.start_download()
    assert state()["state"] == model_store.DOWNLOADING
    model_store.run_downloads()
    assert state()["state"] == model_store.INSTALLED
    assert state()["bytes_done"] == len(BODY)
    assert target(tmp_path).read_bytes() == BODY
    home = tmp_path / "models/huggingface/hub/models--Org--Model"
    assert (home / "refs/main").read_text() == "abc123"
    assert (home / "refs/v1.0").read_text() == "abc123"
    assert model_store.search_ready()


def test_a_killed_download_resumes_from_its_partial_file(store, tmp_path):
    model_store.start_download()
    part = target(tmp_path).with_name("weights.bin.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(BODY[:5000])
    model_store.resume_at_startup()  # a restart finds it mid-way
    model_store.run_downloads()
    assert store.ranges == ["bytes=5000-"]
    assert target(tmp_path).read_bytes() == BODY
    assert state()["state"] == model_store.INSTALLED


def test_a_dropped_connection_fails_with_a_reason_and_can_go_again(store, tmp_path):
    store.fail_after = 100
    model_store.start_download()
    model_store.run_downloads()
    assert state()["state"] == model_store.FAILED
    assert state()["reason"] == model_store.NO_CONNECTION
    store.fail_after = None
    model_store.start_download()
    model_store.run_downloads()
    assert state()["state"] == model_store.INSTALLED
    assert store.ranges[-1] == "bytes=100-"


def test_a_hash_mismatch_fails_and_removes_the_file(store, tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "manifest", lambda: manifest("0" * 64))
    model_store.start_download()
    model_store.run_downloads()
    assert state()["state"] == model_store.FAILED
    assert state()["reason"] == model_store.MISMATCH
    assert not target(tmp_path).exists()


def test_offline_refuses_a_download(store):
    settings.put(settings.OFFLINE, True)
    with pytest.raises(refusals.Refused) as refused:
        model_store.start_download()
    assert refused.value.status == 409


def test_offline_fails_one_found_midway_on_restart(store):
    model_store.start_download()
    settings.put(settings.OFFLINE, True)
    model_store.resume_at_startup()
    assert state()["state"] == model_store.FAILED
    assert state()["reason"] == model_store.OFFLINE_REASON


@pytest.mark.parametrize(
    ("memory", "tag"),
    [(48, "gemma4:26b"), (64, "gemma4:26b"), (24, "gemma4:12b"), (16, "qwen3.5:9b")],
)
def test_the_local_model_follows_memory(memory, tag):
    assert model_store.default_local_tag(memory) == tag


def test_the_worker_waits_for_the_search_models(store, monkeypatch):
    monkeypatch.setattr(pacing.macos, "on_power_or_enough_battery", lambda: True)
    assert pacing.Pacing().may_work() is False
    model_store.start_download()
    model_store.run_downloads()
    assert pacing.Pacing().may_work() is True


def test_nothing_reaches_for_hugging_faces_servers_after_setup(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("SECUREFOLIO_MODEL_CACHE", str(tmp_path))
    vault.use_model_cache()
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_a_part_killed_after_its_last_byte_is_promoted_with_no_request(store, tmp_path):
    model_store.start_download()
    part = target(tmp_path).with_name("weights.bin.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(BODY)
    model_store.run_downloads()
    assert store.ranges == []
    assert state()["state"] == model_store.INSTALLED


def test_a_refused_range_starts_again(store, tmp_path):
    model_store.start_download()
    part = target(tmp_path).with_name("weights.bin.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(BODY[:100])
    store.refuse_ranges = True
    model_store.run_downloads()
    assert store.ranges == ["bytes=100-", None]
    assert target(tmp_path).read_bytes() == BODY


def test_a_part_too_long_is_thrown_away(store, tmp_path):
    model_store.start_download()
    part = target(tmp_path).with_name("weights.bin.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(BODY + b"more")
    model_store.run_downloads()
    assert store.ranges == [None]
    assert target(tmp_path).read_bytes() == BODY


def test_a_row_saying_installed_doesnt_outlive_the_files(store, tmp_path):
    model_store.start_download()
    model_store.run_downloads()
    target(tmp_path).unlink()
    assert state()["state"] == model_store.NOT_INSTALLED
    assert not model_store.search_ready()


def test_files_already_here_are_hashed_before_they_count(store, tmp_path, monkeypatch):
    target(tmp_path).parent.mkdir(parents=True)
    target(tmp_path).write_bytes(BODY)
    assert state()["state"] == model_store.NOT_INSTALLED
    settings.put(settings.OFFLINE, True)
    model_store.resume_at_startup()
    assert state()["state"] == model_store.VERIFYING
    model_store.run_downloads()
    assert state()["state"] == model_store.INSTALLED
    assert store.ranges == []


def test_pause_stops_the_thread_and_its_row_stays(store, tmp_path, monkeypatch):
    model_store.start_download()
    model_store._stopping.set()  # noqa: SLF001
    model_store.run_downloads()
    model_store._stopping.clear()  # noqa: SLF001
    assert state()["state"] == model_store.DOWNLOADING


def test_a_late_ollama_list_doesnt_replace_a_newer_one():
    # An older request's list, answered last, replaced the newer.
    model_store.remember_ollama([{"name": "new:1b", "remote": False}], asked=2.0)
    model_store.remember_ollama([{"name": "old:1b", "remote": False}], asked=1.0)
    assert model_store.installed("new:1b") is True
    assert model_store.installed("old:1b") is False
