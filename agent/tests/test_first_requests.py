"""A first question's request is what it was before answers had their figures checked:
for Claude, for a cloud model citing with markers, and for the local model. Each is
compared with the request recorded before that change, kept in first_requests/, its
random ids put as <id>, as the same question over the same lease asks it."""

import json
import re
from pathlib import Path

import pytest

from backend import keychain, settings, sources, states
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OLLAMA_ENV, STUB_OPENAI_ENV
from testing.ask_helpers import (
    ask,
    cited_reply,
    message,
    new_thread,
    plan_reply,
    system_of,
)
from testing.queue import drain
from testing.stub_models import (
    completion,
    openai_reply,
    stub_anthropic,
    stub_ollama,
    stub_openai,
)

FIXTURES = Path(__file__).with_name("first_requests")
LEASE = "Marker rent. The rent is due monthly at $2,350.\n"
RENT = "The rent is $2,350 a month, or $28,200 a year."
QUESTION = "What is the rent?"
IDS = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b[0-9a-f]{32}\b|\b[0-9a-f]{16}(?=-\d)"  # documents, revisions, chunks
)
SOURCE = '<source id=\\"'


def normalized(raw: str) -> str:
    """The request's bytes, its random ids put as <id>, its JSON keys as sent."""
    return IDS.sub("<id>", raw)


@pytest.fixture
def lease(library, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "Lease.txt").write_text(LEASE)
    sources.add_source(str(root), states.STANDARD)
    drain()


def claude_first_request(app_client, monkeypatch) -> str:
    keychain.set_key("test-not-a-real-key")

    def reply(_path: str, body: dict) -> dict:
        if "search query that stands alone" in system_of(body):
            return plan_reply(body)
        if "Keep what a later follow-up" in system_of(body):
            return message(body, [{"type": "text", "text": "A short summary."}])
        return cited_reply(body, RENT)

    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = reply
        ask(app_client, new_thread(app_client), QUESTION)
    (answer,) = [r.raw for r in stub.requests if "search_result" in r.raw]
    return answer


def marker_first_request(app_client, monkeypatch) -> str:
    for provider in ("anthropic", "openai"):
        keychain.set_key(f"test-{provider}-key", provider)
    settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
    settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")

    def reply(path: str, body: dict) -> dict:
        if SOURCE not in json.dumps(body):
            return openai_reply(path, body)
        return completion(body, f"{RENT} [1]")

    with stub_openai() as stub:
        monkeypatch.setenv(STUB_OPENAI_ENV, stub.url)
        stub.reply = reply
        ask(app_client, new_thread(app_client), QUESTION)
    (answer,) = [r.raw for r in stub.requests if SOURCE in r.raw]
    return answer


def local_first_request(app_client, monkeypatch) -> str:
    """With no key at all, the local model answers."""
    for provider in ("anthropic", "openai"):
        keychain.delete_key(provider)

    def reply(path: str, body: dict) -> dict:
        if path == "/api/tags":
            return {"models": [{"name": "gemma4:26b"}]}
        return {
            "model": body.get("model", "stub"),
            "message": {"role": "assistant", "content": f"{RENT} [1]"},
            "done": True,
            "done_reason": "stop",
        }

    with stub_ollama() as stub:
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        stub.reply = reply
        ask(app_client, new_thread(app_client), QUESTION)
    (answer,) = [r.raw for r in stub.requests if SOURCE in r.raw]
    return answer


PATHS = {
    "claude": claude_first_request,
    "markers": marker_first_request,
    "local": local_first_request,
}


@pytest.mark.parametrize("path", PATHS)
def test_a_first_questions_request_is_as_it_was_before_this_phase(
    path, lease, app_client, monkeypatch
):
    sent = normalized(PATHS[path](app_client, monkeypatch))
    assert sent == (FIXTURES / f"{path}.txt").read_text()
