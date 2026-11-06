"""Settings' routes: the key, a model per task, Offline's gate, spending, pausing."""

import asyncio
import time

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from agent import models
from backend import (
    agent_service,
    connectivity,
    database,
    egress,
    keychain,
    model_store,
    policy,
    settings,
    sources,
    spend,
    vault,
)
from backend.build_mode import STUB_ANTHROPIC_ENV
from backend.main import create_app
from conftest import LOOPBACK, TEST_TOKENS
from testing.ask_helpers import default_reply
from testing.queue import drain
from testing.stub_models import stub_anthropic


@pytest.fixture
def cloud(monkeypatch):
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = default_reply
        yield stub
    keychain.delete_key()


class TestKey:
    def test_a_good_key_is_stored_only_once_it_works(self, app_client, cloud):
        assert app_client.get("/api/settings/key").json()["present"] is False
        key = "sk-ant-goodkey-7Qx2"
        made = app_client.post("/api/settings/key/test", json={"key": key})
        assert made.status_code == 200
        status = app_client.get("/api/settings/key").json()
        assert status == {"present": True, "hint": keychain.hint(), "workspace": None}
        assert key not in str(status)
        assert len(cloud.requests) == 1
        assert cloud.requests[0].body["max_tokens"] == 1

    def test_a_refused_key_shows_the_message_and_stores_nothing(
        self, app_client, cloud, monkeypatch
    ):
        cloud.status = 401
        made = app_client.post("/api/settings/key/test", json={"key": "sk-bad"})
        assert made.status_code == 409
        assert "didn't accept this key" in made.json()["detail"]
        assert keychain.get_key() is None

    def test_the_key_is_in_no_file_in_the_vault(self, app_client, cloud):
        key = "sk-ant-findme-zq4471"
        app_client.post("/api/settings/key/test", json={"key": key})
        for path in vault.root().rglob("*"):
            if path.is_file():
                assert key.encode() not in path.read_bytes(), path

    def test_the_call_is_in_the_log_as_a_key_test(self, app_client, cloud):
        app_client.post("/api/settings/key/test", json={"key": "sk-ant-x"})
        log = app_client.get("/api/spending").json()["log"]
        assert log[0]["purpose"] == "key_test"
        assert log[0]["outcome"] == "sent"

    def test_the_log_names_each_calls_provider_and_model(self, app_client):
        for model in (
            "anthropic:claude-haiku-5-5",
            "openai:gpt-6-luna",
            "openrouter:someone/not-in-the-list",
        ):
            egress.record(egress.Entry(model, "card", "sent", []))
        log = app_client.get("/api/spending").json()["log"]
        assert [(c["provider_name"], c["model_name"]) for c in log] == [
            ("OpenRouter", "someone/not-in-the-list"),
            ("OpenAI", "GPT-6 Luna"),
            ("Anthropic", "Claude Haiku 5.5"),
        ]

    def test_offline_sends_no_test(self, app_client, cloud):
        settings.put(settings.OFFLINE, True)
        made = app_client.post("/api/settings/key/test", json={"key": "sk-ant-x"})
        assert made.status_code == 409
        assert cloud.requests == []

    def test_nothing_to_test(self, app_client):
        assert app_client.post("/api/settings/key/test", json={}).status_code == 422


class TestWorkspace:
    def test_a_key_needing_a_workspace_says_so(self, app_client, cloud):
        cloud.status = 400
        cloud.reply = lambda _path, _body: {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "This API key is not scoped to a workspace.",
            },
        }
        made = app_client.post("/api/settings/key/test", json={"key": "sk-ant-x"})
        assert made.status_code == 409
        assert "Workspace ID" in made.json()["detail"]
        assert keychain.get_key() is None

    def test_a_named_workspace_is_sent_and_kept(self, app_client, cloud):
        made = app_client.post(
            "/api/settings/key/test", json={"key": "sk-ant-x", "workspace": "wrkspc_1"}
        )
        assert made.status_code == 200
        assert settings.get(settings.ANTHROPIC_WORKSPACE) == "wrkspc_1"
        assert cloud.requests[0].headers["anthropic-workspace-id"] == "wrkspc_1"


class TestSettings:
    def test_a_model_per_task_takes_effect_with_no_restart(self, app_client):
        keychain.set_key("sk-ant-x")
        made = app_client.patch(
            "/api/settings", json={"model_answer": "anthropic:claude-opus-5-5"}
        )
        assert made.status_code == 200
        assert settings.get(settings.MODEL_ANSWER) == "anthropic:claude-opus-5-5"
        assert app_client.get("/api/settings").json()["model_answer"] == (
            "anthropic:claude-opus-5-5"
        )
        keychain.delete_key()

    def test_an_unknown_model_is_refused(self, app_client):
        made = app_client.patch("/api/settings", json={"model_answer": "gpt"})
        assert made.status_code == 422

    def test_a_cap_must_be_above_zero(self, app_client):
        made = app_client.patch("/api/settings", json={"spend_cap_usd": 0})
        assert made.status_code == 422

    def test_spending_shows_the_month_and_cap(self, app_client):
        found = app_client.get("/api/spending").json()
        assert found["spent_usd"] == 0
        assert found["prices_as_of"] == spend.PRICES_AS_OF


class TestOffline:
    def test_refused_until_every_model_is_here(self, app_client, monkeypatch):
        monkeypatch.setattr(agent_service, "local_ready", lambda *_given: False)
        made = app_client.put("/api/settings/offline", json={"on": True})
        assert made.status_code == 409
        assert settings.get(settings.OFFLINE) is False

    def test_refused_without_the_search_models(self, app_client, monkeypatch):
        monkeypatch.setattr(agent_service, "local_ready", lambda *_given: True)
        monkeypatch.setattr(model_store, "all_ready", lambda: False)
        made = app_client.put("/api/settings/offline", json={"on": True})
        assert made.status_code == 409

    def test_allowed_once_they_are(self, app_client, monkeypatch):
        monkeypatch.setattr(agent_service, "local_ready", lambda *_given: True)
        monkeypatch.setattr(model_store, "all_ready", lambda: True)
        assert (
            app_client.put("/api/settings/offline", json={"on": True}).status_code
            == 200
        )
        assert settings.get(settings.OFFLINE) is True
        assert (
            app_client.put("/api/settings/offline", json={"on": False}).status_code
            == 200
        )


def test_the_menu_bars_pause_toggles(app_client):
    assert app_client.post("/api/indexing/toggle-pause").json() == {"paused": True}
    assert settings.get("indexing_paused") is True
    assert app_client.post("/api/indexing/toggle-pause").json() == {"paused": False}


def test_models_reports_ollama_and_the_tag(app_client, monkeypatch):
    monkeypatch.setattr(
        agent_service,
        "ollama_status",
        lambda: {"running": True, "tags": ["gemma4:26b"], "refused": None},
    )
    monkeypatch.setattr(model_store, "no_cloud_is_set", lambda: False)
    found = app_client.get("/api/models").json()
    assert found["local"]["installed"] is True
    assert found["ollama"]["no_cloud"] is False
    assert found["search"]["state"] == "installed"


def test_a_non_loopback_ollama_is_refused(library, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://192.0.2.1:11434")
    found = models.ollama_status()
    assert found["running"] is False
    assert "this Mac" in found["refused"]


def test_the_routes_need_the_token(library):
    with TestClient(
        create_app(8000, TEST_TOKENS, write_runtime=False), base_url=LOOPBACK
    ) as client:
        assert client.get("/api/settings").status_code in (401, 403)


def test_the_local_pull_reports_progress(library, monkeypatch):
    done = []

    def pull(tag, advance):
        advance(5, 10)
        done.append(tag)

    monkeypatch.setattr(model_store, "_pull", dict(model_store._pull))  # noqa: SLF001
    model_store.start_local_pull("gemma4:26b", model_store.Pull(pull))
    for _ in range(100):
        if model_store.pull_status()["state"] == model_store.INSTALLED:
            break
        time.sleep(0.01)
    assert done == ["gemma4:26b"]
    assert model_store.pull_status()["total"] == 10


class TestSkippingTheKey:
    def test_a_question_goes_to_the_local_model(self, library):
        assert policy.route("standard", include_private=False).local is True

    def test_a_cloud_call_with_no_key_counts_as_no_connection(self, library):
        call = models.CloudCall(
            policy.Purpose.ANSWER,
            "anthropic:claude-sonnet-5-5",
            "s",
            [],
            lambda _p: [],
            None,
            5,
        )
        with pytest.raises(models.NoConnection):
            asyncio.run(models.ask_cloud(call))


def test_the_label_is_the_model_set(app_client, monkeypatch):
    keychain.set_key("sk-ant-x")
    monkeypatch.setattr(connectivity, "reachable", lambda *_given: True)
    settings.put(settings.MODEL_ANSWER, "anthropic:claude-opus-5-5")
    settings.put(settings.MODEL_DEEP, "anthropic:claude-sonnet-5-5")
    assert policy.route("standard", False).name == "Claude Opus 5.5"
    assert policy.route("deep", False).name == "Claude Sonnet 5.5"
    keychain.delete_key()


def test_a_local_helper_setting_keeps_helpers_on_this_mac(library, cloud, monkeypatch):
    settings.put(settings.MODEL_HELPER, settings.LOCAL)
    keychain.set_key("sk-ant-x")
    asked = []

    async def local(system, _body, _on_text=None, tag=None):
        asked.append(system)
        return models.Reply(text="Done.", content="Done.", local=True)

    monkeypatch.setattr(models, "ask_local", local)
    task = models.Task(policy.Purpose.SUMMARY, "Summarize.", [], None, False)
    assert asyncio.run(models.helper_text(task)) == "Done."
    assert cloud.requests == []


def test_offline_turned_on_as_a_key_test_leaves_stops_it(library, cloud, monkeypatch):
    log = models.api.log_attempt

    def goes_offline(*given):
        made = log(*given)
        settings.put(settings.OFFLINE, True)
        return made

    monkeypatch.setattr(models.api, "log_attempt", goes_offline)
    with pytest.raises(models.CloudRefused):
        models.check_key("anthropic", "sk-ant-x")
    assert cloud.requests == []


def test_an_import_with_no_level_takes_the_default(app_client, tmp_path):
    settings.put(settings.DEFAULT_SENSITIVITY, "private")
    dropped = tmp_path / "Dropped.txt"
    dropped.write_text("Words.\n")
    made = app_client.post("/api/imports", json={"paths": [str(dropped)]})
    assert made.status_code == 200
    with database.read() as connection:
        level = connection.execute(
            sa.select(database.documents.c.sensitivity)
        ).scalar_one()
    assert level == "private"


def test_a_passage_link_finds_its_element(app_client, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "Note.txt").write_text("Words.\n")
    sources.add_source(str(root), "standard")
    drain()
    with database.read() as connection:
        row = connection.execute(sa.select(database.chunks)).mappings().one()
    found = app_client.get(f"/api/documents/{row['document_id']}/chunks/{row['id']}")
    assert found.json() == {"element_id": row["element_ids"][0]}
    gone = app_client.get(f"/api/documents/{row['document_id']}/chunks/none")
    assert gone.json() == {"element_id": None}
