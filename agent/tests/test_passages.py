"""Passages when no model can answer: the run isn't refused; it searches the chat's
scope with the owner's own words, on this Mac, and saves what it finds as passages,
never as an answer."""

import dataclasses
import json
import time

import pytest

from agent import harness
from agent.budgets import STANDARD
from backend import (
    agent_service,
    database,
    keychain,
    settings,
    sources,
    states,
    threads,
)
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OLLAMA_ENV
from testing.ask_helpers import (
    ask,
    cited_reply,
    finish,
    last_answer,
    message,
    new_thread,
    plan_reply,
    system_of,
)
from testing.queue import drain
from testing.stub_models import stub_anthropic, stub_ollama

GONE = "http://127.0.0.1:9"  # nothing listens there: Ollama isn't running


def claude(_path: str, body: dict) -> dict:
    if "search query that stands alone" in system_of(body):
        return plan_reply(body)
    if "Keep what a later follow-up" in system_of(body):
        return message(body, [{"type": "text", "text": "A short summary."}])
    return cited_reply(body)


@pytest.fixture
def folder(library, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "Lease.txt").write_text("Marker rent. The rent is due monthly at $2,350.\n")
    (root / "Roof.txt").write_text("Marker roof. Two flashing seams need resealing.\n")
    sources.add_source(str(root), states.STANDARD)
    drain()


@pytest.fixture
def cloud(monkeypatch):
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = claude
        yield stub


@pytest.fixture
def no_ollama(monkeypatch):
    monkeypatch.setenv(STUB_OLLAMA_ENV, GONE)
    monkeypatch.setattr(
        agent_service.models, "OLLAMA_FRESH_SECONDS", 0.0
    )  # asked afresh


@pytest.fixture
def empty_ollama(monkeypatch):
    """Ollama runs, but has no model: the local one is gone."""
    with stub_ollama() as stub:
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        stub.reply = lambda path, _body: {"models": []} if path == "/api/tags" else {}
        yield stub


def ids_of(title: str) -> list[str]:
    with database.read() as connection:
        return list(
            connection.execute(
                database.documents.select()
                .with_only_columns(database.documents.c.id)
                .where(database.documents.c.title == title)
            ).scalars()
        )


def passages_for(app_client, thread: str, question: str) -> dict:
    found = ask(app_client, thread, question)
    answer = last_answer(found)
    assert answer["kind"] == "passages"
    return answer


def test_with_no_key_and_the_local_model_gone_it_finds_passages(
    folder, cloud, empty_ollama, app_client
):
    keychain.delete_key()
    settings.put(settings.MODEL_HELPER, settings.LOCAL)  # else the helper's reason
    found = passages_for(app_client, new_thread(app_client), "When is rent due?")
    assert "is no longer in Ollama" in found["reason"]
    assert cloud.requests == []


def test_a_model_with_no_known_price_finds_passages(folder, cloud, app_client):
    keychain.set_key("test-not-a-real-key")
    settings.put(settings.MODEL_ANSWER, "openrouter:nobody/unpriced")
    found = passages_for(app_client, new_thread(app_client), "When is rent due?")
    assert "No known price" in found["reason"]
    assert cloud.requests == []


def test_a_helper_needing_a_key_with_no_local_model_finds_passages(
    folder, cloud, empty_ollama, app_client
):
    keychain.set_key("test-not-a-real-key")
    keychain.delete_key("openai")
    settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")
    found = passages_for(app_client, new_thread(app_client), "When is rent due?")
    assert "The helper model needs OpenAI's key" in found["reason"]
    assert cloud.requests == []


def test_a_block_found_as_the_run_begins_finds_passages_too(
    folder, cloud, app_client, monkeypatch
):
    """The route was clear when the question was sent, and closed before any model
    call: still passages, never a failure."""
    keychain.set_key("test-not-a-real-key")
    routed = harness.api.route

    def closing(*given, **named):
        return dataclasses.replace(routed(*given, **named), blocked="It just closed.")

    monkeypatch.setattr(harness.api, "route", closing)
    found = passages_for(app_client, new_thread(app_client), "When is rent due?")
    assert found["reason"] == "It just closed."
    assert cloud.requests == []


def test_its_activity_says_where_it_searched_and_why(
    folder, cloud, no_ollama, app_client
):
    scope = {"kind": "library", "include_private": True, "label": "All"}
    found = passages_for(app_client, new_thread(app_client, scope), "When is rent due?")
    searched, why = found["activity"]
    # The count of documents searched, and
    # the whole line read. The folder's two files are the documents searched.
    assert found["coverage"]["searched"] == 2
    assert searched == "Searched “When is rent due?” in 2 documents, on this Mac"
    assert why.startswith("No model answered: Ollama isn't running")


def test_a_follow_up_says_so(folder, cloud, no_ollama, app_client):
    scope = {"kind": "library", "include_private": True, "label": "All"}
    thread = new_thread(app_client, scope)
    assert passages_for(app_client, thread, "When is rent due?")["follow_up"] is False
    assert passages_for(app_client, thread, "And the roof?")["follow_up"] is True


def test_nothing_found_says_how_many_documents_it_searched(
    folder, cloud, no_ollama, app_client
):
    keychain.delete_key()  # a key an earlier test left would let Claude answer
    made = app_client.post("/api/collections", json={"name": "Empty"}).json()
    scope = {"kind": "collection", "id": made["id"], "label": "Empty"}
    found = passages_for(app_client, new_thread(app_client, scope), "When is rent?")
    assert found["found"] == []
    assert found["coverage"]["searched"] == 0


def test_passages_are_never_a_note_nor_a_turn_a_follow_up_reads(
    folder, cloud, no_ollama, app_client, monkeypatch
):
    keychain.delete_key()
    thread = new_thread(app_client)
    asked = ask(app_client, thread, "When is rent due?")
    saved = asked["messages"][-1]
    assert saved["answer"]["kind"] == "passages"
    assert app_client.post(f"/api/messages/{saved['id']}/note").status_code == 404
    # A model can answer the next question: it reads the earlier question, and no
    # answer to it, for passages found aren't one.
    keychain.set_key("test-not-a-real-key")
    answered = last_answer(ask(app_client, thread, "And the roof?"))
    assert answered["kind"] == "answer"
    (request,) = [json.loads(r.raw) for r in cloud.requests if "search_result" in r.raw]
    roles = [m["role"] for m in request["messages"]]
    assert "assistant" not in roles[:-1]


def test_its_first_step_shows_within_250_ms(folder, cloud, no_ollama, app_client):
    scope = {"kind": "library", "include_private": True, "label": "All"}
    thread = new_thread(app_client, scope)
    started = time.perf_counter()
    run = app_client.post(
        f"/api/threads/{thread}/messages",
        json={"text": "When is rent due?", "mode": "standard"},
    ).json()
    while not any(e["event"] == "step" for e in threads.events_after(run["id"], 0)):
        time.sleep(0.005)
        if time.perf_counter() - started > 1:
            break  # long past: the assert below says by how much
    assert time.perf_counter() - started < 0.25


def test_its_first_step_shows_before_a_slow_ollama_answers(
    folder, cloud, no_ollama, app_client, monkeypatch
):
    """Ollama's list is asked inside the run, after
    its first step, so an Ollama a second slow to answer never holds that back."""
    asked = agent_service.models.refresh_ollama_list

    def slow() -> None:
        time.sleep(1)
        asked()

    monkeypatch.setattr(agent_service.models, "refresh_ollama_list", slow)
    scope = {"kind": "library", "include_private": True, "label": "All"}
    thread = new_thread(app_client, scope)
    started = time.perf_counter()
    run = app_client.post(
        f"/api/threads/{thread}/messages",
        json={"text": "When is rent due?", "mode": "standard"},
    ).json()
    while not any(e["event"] == "step" for e in threads.events_after(run["id"], 0)):
        time.sleep(0.005)
        if time.perf_counter() - started > 2:
            break  # long past: the assert below says by how much
    assert time.perf_counter() - started < 0.25
    assert last_answer(finish(app_client, run))["kind"] == "passages"


@pytest.fixture
def folding(monkeypatch):
    """A history budget so small that every answered question folds the turns
    before it into the chat's summary."""
    small = dataclasses.replace(STANDARD, turns=1)
    monkeypatch.setattr(harness, "for_mode", lambda _mode: small)


def test_a_passages_run_never_writes_a_summary(folder, cloud, folding, app_client):
    """The turns would fold, and the helper is in the cloud; a
    passages run still calls no model, and the next answer folds them instead."""
    keychain.set_key("test-not-a-real-key")
    thread = new_thread(app_client)
    assert last_answer(ask(app_client, thread, "When is rent due?"))["kind"] == "answer"
    sent = len(cloud.requests)
    settings.put(settings.MODEL_ANSWER, "openrouter:nobody/unpriced")
    found = last_answer(ask(app_client, thread, "And how much is it, in full?"))
    assert found["kind"] == "passages"
    assert len(cloud.requests) == sent, "no summary, nor any other call"


def test_a_follow_up_after_a_summary_still_says_its_words_were_alone(
    folder, cloud, folding, app_client
):
    """The earlier question is folded into the summary, and the
    passages still say they were found with the owner's words alone."""
    keychain.set_key("test-not-a-real-key")
    thread = new_thread(app_client)
    assert last_answer(ask(app_client, thread, "When is rent due?"))["kind"] == "answer"
    settings.put(settings.MODEL_ANSWER, "openrouter:nobody/unpriced")
    found = last_answer(ask(app_client, thread, "And the roof?"))
    assert found["kind"] == "passages"
    assert found["follow_up"] is True
