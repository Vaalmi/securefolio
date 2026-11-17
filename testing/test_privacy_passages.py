"""Passages found when no model can answer stay on this Mac and in the chat's own
scope: a Private chat, a Private folder's chat, and Offline mode."""

import pytest

from backend import agent_service, database, keychain, settings, sources, states
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OLLAMA_ENV
from testing.ask_helpers import ask, last_answer, new_thread
from testing.queue import drain
from testing.stub_models import stub_anthropic


@pytest.fixture
def folder(library, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "Lease.txt").write_text("Marker rent. The rent is due monthly at $2,350.\n")
    sources.add_source(str(root), states.STANDARD)
    drain()


@pytest.fixture
def cloud(monkeypatch):
    """Claude's stub, which records whatever reaches it: here, nothing should."""
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        yield stub


@pytest.fixture
def no_ollama(monkeypatch):
    monkeypatch.setenv(STUB_OLLAMA_ENV, "http://127.0.0.1:9")
    monkeypatch.setattr(agent_service.models, "OLLAMA_FRESH_SECONDS", 0.0)


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
    answer = last_answer(ask(app_client, thread, question))
    assert answer["kind"] == "passages"
    return answer


def test_a_private_chat_with_ollama_down_finds_passages(
    folder, cloud, no_ollama, app_client
):
    scope = {"kind": "library", "include_private": True, "label": "All"}
    found = passages_for(app_client, new_thread(app_client, scope), "When is rent due?")
    assert "Ollama isn't running" in found["reason"]
    assert any("$2,350" in p["text"] for p in found["found"])
    assert cloud.requests == []


def test_a_private_folders_chat_stays_in_its_folder(
    folder, cloud, no_ollama, app_client, tmp_path
):
    secret = tmp_path / "s"
    secret.mkdir()
    (secret / "Tax.txt").write_text("Marker tax. The rent deduction is $1,100.\n")
    sources.add_source(str(secret), states.PRIVATE)
    drain()
    scope = {"kind": "documents", "ids": ids_of("Tax"), "label": "Taxes"}
    found = passages_for(app_client, new_thread(app_client, scope), "What about rent?")
    assert {p["title"] for p in found["found"]} == {"Tax"}
    assert cloud.requests == []


def test_offline_with_nothing_local_finds_passages_and_sends_nothing(
    folder, cloud, no_ollama, app_client
):
    keychain.set_key("test-not-a-real-key")
    settings.put(settings.OFFLINE, True)
    found = passages_for(app_client, new_thread(app_client), "When is rent due?")
    assert found["found"]
    assert cloud.requests == []
