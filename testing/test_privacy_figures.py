"""A Private chat's answer is checked for its figures on this Mac, and its marks,
carried in words into a follow-up, never reach a cloud model."""

import pytest

from backend import database, keychain, sources, states
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OLLAMA_ENV
from testing.ask_helpers import ask, last_answer
from testing.queue import drain
from testing.stub_models import stub_anthropic, stub_ollama

LEASE = "Marker rent. The rent is due monthly at $2,350.\n"


@pytest.fixture
def lease(library, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "Lease.txt").write_text(LEASE)
    sources.add_source(str(root), states.STANDARD)
    drain()


@pytest.fixture
def cloud(monkeypatch):
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        yield stub


def figures_of(answer: dict) -> list[list[tuple[str, str]]]:
    return [
        [(f["text"], f["state"]) for f in part.get("figures") or []]
        for part in answer["parts"]
    ]


def test_a_private_answers_marks_stay_on_this_mac(
    lease, cloud, app_client, tmp_path, monkeypatch
):
    """A local answer resting on a Private passage is checked too; its follow-up,
    marks and all, never reaches the cloud model."""
    secret = tmp_path / "s"
    secret.mkdir()
    (secret / "Diagnosis.txt").write_text(
        "Marker zanzibar. The planted rent is $9,000.\n"
    )
    sources.add_source(str(secret), states.PRIVATE)
    drain()
    with database.read() as connection:
        ids = [row.id for row in connection.execute(database.documents.select())]
    scope = {"kind": "documents", "ids": ids, "label": "Selected"}
    thread = app_client.post("/api/threads", json={"scope": scope}).json()["id"]

    def local(path: str, body: dict) -> dict:
        if path == "/api/tags":
            return {"models": [{"name": "gemma4:26b"}]}
        return {
            "model": body.get("model", "stub"),
            "message": {"role": "assistant", "content": "It is $9,500 a month. [1]"},
            "done": True,
            "done_reason": "stop",
        }

    with stub_ollama() as ollama:
        monkeypatch.setenv(STUB_OLLAMA_ENV, ollama.url)
        ollama.reply = local
        # A scope holding a Private document makes the chat Private from the start.
        found = ask(app_client, thread, "What is the planted rent?")
        assert found["thread"]["private_tainted"] is True
        answer = last_answer(found)
        assert figures_of(answer) == [[("$9,500", "unshown")]]
        ask(app_client, thread, "And next year?")
        assert "(not in its sources)" in ollama.all_text()
    assert cloud.requests == [], "nothing of a Private chat goes to the cloud"
