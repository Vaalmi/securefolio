"""A Private sheet's column names reach no cloud model: they go with the sheet's rows,
and those are answered on this Mac."""

import pytest

from backend import keychain
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OLLAMA_ENV
from testing.ask_helpers import ask, new_thread, system_of
from testing.sheet_ledgers import PLAN, WANTED, add_ledgers, claude_finding, document_id
from testing.stub_models import answers_of, ollama_reply, stub_anthropic, stub_ollama


@pytest.fixture
def sheets(library, tmp_path):
    return add_ledgers(tmp_path)


@pytest.fixture
def cloud(monkeypatch):
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = claude_finding(WANTED)
        yield stub
    keychain.delete_key()


@pytest.fixture
def local(monkeypatch):
    with stub_ollama() as stub:
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)

        def reply(path: str, body: dict) -> dict:
            found = ollama_reply(path, body)
            if path == "/api/chat" and "stands alone" in system_of(body):
                found["message"]["content"] = PLAN
            return found

        stub.reply = reply
        yield stub


def test_a_private_sheets_names_reach_no_cloud_model(sheets, cloud, local, app_client):
    ask(app_client, new_thread(app_client), "Who is Larkspur?")
    hidden = {
        "kind": "documents",
        "ids": [document_id("hidden ledger")],
        "label": "Selected",
    }
    ask(app_client, new_thread(app_client, hidden), "Who is Larkspur?")
    sent = cloud.all_text()
    assert "Columns: Date, Payee, Amount" in sent  # the Standard one's went
    for name in sheets:
        assert name not in sent
    handed = "\n".join(r.raw for r in answers_of(local))
    assert all(name in handed for name in sheets)  # handed on this Mac
