"""Each local job's model: a removed tag blocks answers and holds cards and figures
pending; choosing a suitable model, or downloading it again, requeues that work; a
change is the next question's or job's, and nothing finished is redone."""

import json
import threading
from pathlib import Path

import pytest
import sqlalchemy as sa

from agent import harness
from backend import (
    agent_service,
    cards,
    database,
    jobs,
    model_store,
    settings,
    sources,
    states,
)
from backend.build_mode import STUB_OLLAMA_ENV
from backend.locators import PdfLocator
from testing.ask_helpers import new_thread
from testing.queue import drain
from testing.stub_models import stub_ollama
from worker import hard_pages, main
from worker.content import Element

RECORDED = json.loads((Path(__file__).parent / "fixtures/ollama_0_40.json").read_text())
FIRST, SECOND = "gemma4:e2b-it-qat", "gemma4:e4b"  # both on this Mac, both take images
CARD = {
    "title": "Lease",
    "summary": "A lease.",
    "doc_type": "Contract",
    "dates": [],
    "people": [],
    "organizations": [],
    "amounts": [],
    "tags": [],
}


class Ollama:
    """This Mac's recorded Ollama, with a tag that can be removed, and every chat
    kept by the model it named."""

    def __init__(self) -> None:
        self.gone: set[str] = set()
        self.chats: list[str] = []

    def reply(self, path: str, body: dict) -> dict:
        if path == "/api/tags":
            listed = RECORDED["tags"]["models"]
            return {"models": [m for m in listed if m["name"] not in self.gone]}
        if path == "/api/show":
            return RECORDED["show"][body["model"]]
        if path == "/api/ps":
            return {"models": []}
        self.chats.append(body.get("model", ""))
        content = json.dumps(CARD) if "format" in body else "The rent is due monthly."
        return {
            "model": body.get("model"),
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": "stop",
        }


@pytest.fixture
def ollama(library, monkeypatch):
    monkeypatch.setattr(agent_service, "OLLAMA_FRESH_SECONDS", 0.0)
    monkeypatch.setattr(model_store, "_capabilities", {})
    side = Ollama()
    with stub_ollama() as stub:
        stub.reply = side.reply
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        for job in settings.LOCAL_JOBS:
            settings.put(job, FIRST)
        yield side


def folder(tmp_path, name: str, files: int = 1) -> str:
    root = tmp_path / name
    root.mkdir()
    for n in range(files):
        (root / f"{name} {n}.txt").write_text(f"{name}: the rent is due monthly. {n}\n")
    return sources.add_source(str(root), states.STANDARD)["id"]


def all_cards() -> dict[str, dict]:
    with database.read() as connection:
        ids = connection.execute(sa.select(database.documents.c.id)).scalars().all()
    return {document_id: cards.get(document_id) for document_id in ids}


def local_ready_jobs() -> int:
    with database.read() as connection:
        return connection.execute(
            sa.select(sa.func.count()).where(database.jobs.c.kind == jobs.LOCAL_READY)
        ).scalar_one()


class TestARemovedTag:
    def test_answers_are_blocked_with_the_reason(self, ollama, app_client):
        ollama.gone.add(FIRST)
        route = app_client.get("/api/ask/route", params={"mode": "private"}).json()
        assert (
            route["blocked"]
            == f"{FIRST} is no longer in Ollama. Choose another in Settings."
        )

    def test_cards_wait_as_pending_with_the_reason(self, ollama, tmp_path):
        ollama.gone.add(FIRST)
        folder(tmp_path, "Home")
        drain()
        (card,) = all_cards().values()
        assert card["state"] == states.PENDING
        assert f"{FIRST}, isn't ready" in card["state_reason"]
        assert ollama.chats == []

    def test_figures_wait_as_pending(self, ollama, monkeypatch):
        ollama.gone.add(FIRST)
        where = PdfLocator(page=1, rects=[], precision="page")
        picture = Element("picture", "", where, states.QUEUED)
        monkeypatch.setattr(jobs, "advance_stage", lambda *_given: True)
        hard_pages._read_pictures("job", [picture], {id(picture): b"png"}, FIRST)  # noqa: SLF001
        assert picture.reading_state == states.PENDING
        assert ollama.chats == []


class TestRequeued:
    def test_choosing_a_suitable_model_requeues_the_waiting_cards_only(
        self, ollama, tmp_path, app_client
    ):
        folder(tmp_path, "Done")
        drain()
        finished = dict(all_cards())  # written by FIRST
        assert {c["written_by"] for c in finished.values()} == {FIRST}
        ollama.gone.add(FIRST)
        folder(tmp_path, "Waiting")
        drain()
        assert (
            app_client.patch("/api/settings", json={"local_cards": SECOND}).status_code
            == 200
        )
        assert local_ready_jobs() == 1
        drain()
        now = all_cards()
        for document_id in finished:
            assert now[document_id]["written_by"] == FIRST, "nothing finished is redone"
        waiting = [c for d, c in now.items() if d not in finished]
        assert [c["written_by"] for c in waiting] == [SECOND]
        assert waiting[0]["state"] == states.COMPLETE

    def test_choosing_a_figures_model_requeues_the_waiting_pictures(
        self, ollama, app_client, monkeypatch
    ):
        asked = []
        monkeypatch.setattr(
            hard_pages, "queue_pending_when_ready", lambda: asked.append(1)
        )
        assert (
            app_client.patch(
                "/api/settings", json={"local_figures": SECOND}
            ).status_code
            == 200
        )
        drain()
        assert asked == [1]

    def test_downloading_it_again_requeues_the_waiting_work(self, ollama):
        done = threading.Event()

        def after() -> None:
            agent_service._after_pull()  # noqa: SLF001  what a finished pull runs
            done.set()

        model_store.start_local_pull(
            FIRST, model_store.Pull(lambda _tag, _advance: None, True, after)
        )
        assert done.wait(5)
        assert local_ready_jobs() == 1

    def test_the_worker_handles_it(self):
        assert jobs.LOCAL_READY in main.HANDLERS


class TestAChange:
    def test_a_run_already_going_finishes_on_its_model(
        self, ollama, tmp_path, app_client, monkeypatch
    ):
        folder(tmp_path, "Home")
        drain()
        ollama.chats.clear()
        real = harness._send_local  # noqa: SLF001

        async def changed_first(*given, **named):
            await harness.asyncio.to_thread(
                settings.put, settings.LOCAL_ANSWERS, SECOND
            )
            return await real(*given, **named)

        monkeypatch.setattr(harness, "_send_local", changed_first)
        thread = new_thread(app_client)
        run = app_client.post(
            f"/api/threads/{thread}/messages",
            json={"text": "When is the rent due?", "mode": "private"},
        ).json()
        from testing.ask_helpers import finish  # noqa: PLC0415

        done = finish(app_client, run)
        assert done["messages"][-1]["answer"]["model"] == FIRST
        assert set(ollama.chats) == {FIRST}, "every call of the run on its own model"
        monkeypatch.setattr(harness, "_send_local", real)
        ollama.chats.clear()
        run = app_client.post(
            f"/api/threads/{thread}/messages",
            json={"text": "And how much is it?", "mode": "private"},
        ).json()
        done = finish(app_client, run)
        assert done["messages"][-1]["answer"]["model"] == SECOND, "the next question's"
        assert SECOND in ollama.chats

    def test_a_new_cards_model_is_used_for_the_next_job_with_no_restart(
        self, ollama, tmp_path
    ):
        settings.put(settings.LOCAL_CARDS, SECOND)
        folder(tmp_path, "Home")
        drain()
        assert {c["written_by"] for c in all_cards().values()} == {SECOND}
