"""The live runs' script, scripts/measure/providers_live.py, without the network: what
it sends to the key test and to turn organizing on."""

from typing import Any, cast

from fastapi.testclient import TestClient

from backend import providers, settings
from scripts.measure import providers_live


class Client:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, json: dict[str, Any]) -> Any:
        self.sent.append((url, json))

        class Answer:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return {"hint": "••••1234", "scope_id": json.get("scope_id")}

        return Answer()


def test_the_openai_key_test_takes_the_project_id(monkeypatch):
    # The OpenAI steps tested the key without its Project ID.
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key-live")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj_live")
    client = Client()
    sent = cast("TestClient", client)
    report = providers_live._openai_key_test(sent)  # noqa: SLF001
    (url, body) = client.sent[0]
    assert url == f"/api/settings/keys/{providers.OPENAI}/test"
    assert body == {"key": "test-openai-key-live", "scope_id": "proj_live"}
    assert report["scope_id"] == "proj_live"


def test_without_a_project_id_none_is_sent(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key-live")
    monkeypatch.delenv("OPENAI_PROJECT_ID", raising=False)
    client = Client()
    sent = cast("TestClient", client)
    providers_live._openai_key_test(sent)  # noqa: SLF001
    assert client.sent[0][1] == {"key": "test-openai-key-live"}


def test_a_folder_is_organized_at_the_helpers_price(library, tmp_path):
    body = providers_live._organized_folder("openai:gpt-6-luna", tmp_path)  # noqa: SLF001
    assert body["auto_organize"] is True
    assert (body["model"], body["input_usd"], body["output_usd"]) == (
        "openai:gpt-6-luna",
        0.1,
        0.5,
    )


def test_the_anthropic_step_chooses_anthropics_deep_model(library):
    # Its Deep question went to whatever Deep model was saved.
    settings.put(settings.MODEL_DEEP, "openai:gpt-6.1-sol")
    providers_live._choose_anthropic()  # noqa: SLF001
    assert settings.get(settings.MODEL_DEEP) == "anthropic:claude-opus-5-5"
    assert settings.get(settings.MODEL_ANSWER) == "anthropic:claude-sonnet-5-5"
