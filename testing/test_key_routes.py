"""Settings, Keys: a key per provider, its last four characters only, Test and save,
Test, Remove, the optional IDs, the needs-ID state kept until an ID is added, and
OpenRouter's list with Refresh."""

import pytest

from agent import models
from backend import agent_service, keychain, model_catalog, policy, providers, settings
from backend.build_mode import (
    STUB_ANTHROPIC_ENV,
    STUB_OPENAI_ENV,
    STUB_OPENROUTER_ENV,
)
from testing.stub_models import (
    OPENROUTER_LIST,
    openai_reply,
    stub_anthropic,
    stub_openai,
)

NEEDS_PROJECT = {
    "error": {"message": "This key must be used with a project.", "type": "x"}
}


@pytest.fixture
def stubs(library, monkeypatch):
    with (
        stub_anthropic() as anthropic,
        stub_openai() as openai,
        stub_openai() as router,
    ):
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, anthropic.url)
        monkeypatch.setenv(STUB_OPENAI_ENV, openai.url)
        monkeypatch.setenv(STUB_OPENROUTER_ENV, router.url)
        yield {
            providers.ANTHROPIC: anthropic,
            providers.OPENAI: openai,
            providers.OPENROUTER: router,
        }
    for provider in providers.PROVIDERS:
        keychain.delete_key(provider)


def keys(app_client) -> dict[str, dict]:
    return {row["provider"]: row for row in app_client.get("/api/settings/keys").json()}


def test_a_row_per_provider_showing_only_the_last_four(stubs, app_client):
    keychain.set_key("sk-proj-abcdefgh1234", providers.OPENAI)
    rows = keys(app_client)
    assert set(rows) == set(providers.PROVIDERS)
    assert rows["openai"]["present"] is True
    assert rows["openai"]["hint"].endswith("1234")
    assert "abcdefgh" not in str(rows)
    assert (rows["anthropic"]["id_label"], rows["openai"]["id_label"]) == (
        "Workspace ID",
        "Project ID",
    )
    assert rows["openrouter"]["id_label"] is None
    assert rows["anthropic"]["present"] is False


def test_test_and_save_keeps_a_key_only_once_it_works(stubs, app_client):
    stubs[providers.OPENAI].status = 401
    stubs[providers.OPENAI].reply = lambda _p, _b: {"error": {"message": "bad"}}
    refused = app_client.post(
        "/api/settings/keys/openai/test", json={"key": "sk-proj-bad0"}
    )
    assert refused.status_code == 409
    assert "OpenAI didn't accept this key" in refused.json()["detail"]
    assert keys(app_client)["openai"]["present"] is False
    stubs[providers.OPENAI].status = 200
    stubs[providers.OPENAI].reply = None
    saved = app_client.post(
        "/api/settings/keys/openai/test",
        json={"key": " sk-proj-good5678 ", "scope_id": " proj_9 "},
    )
    assert saved.status_code == 200
    assert saved.json()["hint"].endswith("5678")
    assert saved.json()["scope_id"] == "proj_9", "trimmed"


def test_a_saved_key_that_needs_its_id_stays_marked_until_one_is_added(
    stubs, app_client
):
    keychain.set_key("sk-proj-saved1111", providers.OPENAI)
    stubs[providers.OPENAI].status = 400
    stubs[providers.OPENAI].reply = lambda _p, _b: NEEDS_PROJECT
    needed = app_client.post("/api/settings/keys/openai/test", json={})
    assert needed.status_code == 422
    assert "Add its Project ID" in needed.json()["detail"]
    assert keys(app_client)["openai"]["needs_id"] is True, "kept for reopening Settings"
    url = "/api/settings/keys/openai/scope"
    added = app_client.put(url, json={"scope_id": "proj_1"}).json()
    assert added["needs_id"] is True, "an ID not yet tested hasn't worked"
    cleared = app_client.put(url, json={"scope_id": ""})
    assert cleared.json()["needs_id"] is True, "clearing the ID brings it back"
    stubs[providers.OPENAI].status = 200
    stubs[providers.OPENAI].reply = None
    fixed = app_client.post(
        "/api/settings/keys/openai/test", json={"scope_id": "proj_1"}
    )
    assert fixed.status_code == 200
    assert fixed.json()["needs_id"] is False
    assert fixed.json()["scope_id"] == "proj_1"
    again = app_client.put(url, json={"scope_id": ""})
    assert again.json()["needs_id"] is True, "it worked with its ID, so still needs one"


def test_the_id_is_added_changed_and_cleared(stubs, app_client):
    keychain.set_key("sk-ant-k1", providers.ANTHROPIC)
    url = "/api/settings/keys/anthropic/scope"
    assert (
        app_client.put(url, json={"scope_id": " wrkspc_1 "}).json()["scope_id"]
        == "wrkspc_1"
    )
    assert app_client.put(url, json={"scope_id": ""}).json()["scope_id"] is None
    refused = app_client.put(
        "/api/settings/keys/openrouter/scope", json={"scope_id": "x"}
    )
    assert refused.status_code == 422


def test_remove_takes_the_key_its_id_and_its_mark(stubs, app_client):
    keychain.set_key("sk-ant-k1", providers.ANTHROPIC)
    settings.put(settings.ANTHROPIC_WORKSPACE, "wrkspc_1")
    settings.put(settings.ANTHROPIC_NEEDS_ID, True)
    gone = app_client.delete("/api/settings/keys/anthropic").json()
    assert (gone["present"], gone["scope_id"], gone["needs_id"]) == (False, None, False)
    assert keychain.get_key(providers.ANTHROPIC) is None


def test_an_unknown_provider_is_refused(stubs, app_client):
    assert app_client.delete("/api/settings/keys/someone").status_code == 404


def test_openrouters_list_has_its_date_and_refresh(stubs, app_client):
    keychain.set_key("sk-or-k1", providers.OPENROUTER)
    assert app_client.get("/api/models/openrouter").json()["state"] == "none"
    fresh = app_client.post("/api/models/openrouter/refresh").json()
    assert fresh["state"] == "fresh"
    assert fresh["fetched_at"] is not None
    assert model_catalog.get(f"openrouter:{OPENROUTER_LIST[0]['id']}") is not None


def test_refresh_is_refused_in_offline_mode(stubs, app_client):
    settings.put(settings.OFFLINE, True)
    refused = app_client.post("/api/models/openrouter/refresh")
    assert refused.status_code == 409
    assert stubs[providers.OPENROUTER].requests == []


def needs(app_client) -> bool:
    return keys(app_client)["openai"]["needs_id"]


@pytest.fixture
def needs_project(stubs):
    """A saved OpenAI key that needs its Project ID: proj_right is the one."""
    keychain.set_key("sk-proj-saved1111", providers.OPENAI)

    stub = stubs[providers.OPENAI]

    def answer(path: str, body: dict) -> dict:
        sent = {k.casefold(): v for k, v in stub.requests[-1].headers.items()}
        project = sent.get("openai-project")
        if project == "proj_right":
            stub.status = 200
            return openai_reply(path, body)
        stub.status = 400
        return NEEDS_PROJECT

    stub.reply = answer
    return stub


def test_a_saved_id_its_test_refused_keeps_the_needs_id_state(
    needs_project, app_client
):
    # The wrong ID saved, and the saved key's test refused for it.
    url = "/api/settings/keys/openai/scope"
    test = "/api/settings/keys/openai/test"
    app_client.put(url, json={"scope_id": "proj_wrong"})
    assert app_client.post(test, json={}).status_code == 422
    assert (keys(app_client)["openai"]["scope_id"], needs(app_client)) == (
        "proj_wrong",
        True,
    )
    assert policy.key_ready(providers.OPENAI) is False


def test_no_sequence_of_tests_hides_it_until_the_saved_id_works(
    needs_project, app_client
):
    # Two refused tests with different IDs, then every other order.
    url = "/api/settings/keys/openai/scope"
    test = "/api/settings/keys/openai/test"
    app_client.put(url, json={"scope_id": "proj_wrong_a"})
    assert app_client.post(test, json={}).status_code == 422
    assert app_client.post(test, json={"scope_id": "proj_wrong_b"}).status_code == 422
    assert needs(app_client) is True, "the saved proj_wrong_a still hasn't worked"
    assert policy.key_ready(providers.OPENAI) is False
    app_client.put(url, json={"scope_id": "proj_untested"})
    assert needs(app_client) is True, "an ID not yet tested hasn't worked either"
    assert app_client.post(test, json={"scope_id": "proj_right"}).status_code == 200
    assert needs(app_client) is False
    assert policy.key_ready(providers.OPENAI) is True
    app_client.put(url, json={"scope_id": ""})
    assert needs(app_client) is True, "cleared, it needs one again"
    app_client.put(url, json={"scope_id": "proj_right"})
    assert needs(app_client) is False, "the one it worked with, back again"
    assert app_client.post(test, json={"scope_id": "proj_wrong_c"}).status_code == 422
    assert needs(app_client) is False, "a typed ID refused doesn't touch the saved one"


def test_a_key_tested_with_its_id_emptied_keeps_no_old_id(stubs, app_client):
    # The ID kept is the one the key was tested with.
    keychain.set_key("test-openai-key-old", providers.OPENAI)
    settings.put(settings.OPENAI_PROJECT, "proj_old")
    saved = app_client.post(
        "/api/settings/keys/openai/test",
        json={"key": "test-openai-key-new", "scope_id": ""},
    )
    assert saved.status_code == 200
    assert saved.json()["scope_id"] is None
    assert settings.get(settings.OPENAI_PROJECT) is None
    (sent,) = stubs[providers.OPENAI].requests
    assert "openai-project" not in {name.casefold() for name in sent.headers}
    assert keys(app_client)["openai"]["needs_id"] is False


def test_a_key_tested_with_no_id_given_keeps_the_saved_one(stubs, app_client):
    keychain.set_key("test-openai-key-old", providers.OPENAI)
    settings.put(settings.OPENAI_PROJECT, "proj_1")
    saved = app_client.post("/api/settings/keys/openai/test", json={})
    assert saved.status_code == 200
    assert saved.json()["scope_id"] == "proj_1"


@pytest.fixture
def meanwhile(monkeypatch):
    """Runs a change while a key test waits for its provider, as a request sent then
    would; the test's own answer comes after."""

    def during(change, answer=None):
        def check(*_args):
            change()
            if answer is not None:
                raise answer

        monkeypatch.setattr(models, "check_key", check)
        return agent_service

    return during


def test_a_key_removed_while_its_test_waits_stays_removed(stubs, app_client, meanwhile):
    # A late pass put the removed key back.
    keychain.set_key("test-openai-key-old", providers.OPENAI)
    settings.put(settings.OPENAI_PROJECT, "proj_1")
    service = meanwhile(lambda: service.remove_key(providers.OPENAI))
    late = app_client.post("/api/settings/keys/openai/test", json={})
    assert late.status_code == 409
    assert "changed while it was tested" in late.json()["detail"]
    assert keychain.get_key(providers.OPENAI) is None
    assert settings.get(settings.OPENAI_PROJECT) is None
    assert policy.key_ready(providers.OPENAI) is False


def test_an_id_saved_while_a_test_waits_is_the_one_kept(stubs, app_client, meanwhile):
    keychain.set_key("test-openai-key-old", providers.OPENAI)
    settings.put(settings.OPENAI_PROJECT, "proj_1")
    service = meanwhile(lambda: service.set_key_scope(providers.OPENAI, "proj_2"))
    late = app_client.post("/api/settings/keys/openai/test", json={})
    assert late.status_code == 409
    assert settings.get(settings.OPENAI_PROJECT) == "proj_2"


def test_a_late_needs_id_answer_marks_no_removed_key(stubs, app_client, meanwhile):
    keychain.set_key("test-openai-key-old", providers.OPENAI)
    service = meanwhile(
        lambda: service.remove_key(providers.OPENAI), models.IdNeeded("no project")
    )
    late = app_client.post("/api/settings/keys/openai/test", json={})
    assert late.status_code == 409
    assert keys(app_client)["openai"]["needs_id"] is False


def test_the_older_key_route_keeps_a_removal_too(stubs, app_client, meanwhile):
    keychain.set_key("sk-ant-k1", providers.ANTHROPIC)
    service = meanwhile(lambda: service.remove_key(providers.ANTHROPIC))
    late = app_client.post("/api/settings/key/test", json={})
    assert late.status_code == 409
    assert keychain.get_key(providers.ANTHROPIC) is None


def test_a_test_no_change_overtook_still_saves(stubs, app_client, meanwhile):
    meanwhile(lambda: None)
    saved = app_client.post(
        "/api/settings/keys/openai/test",
        json={"key": "test-openai-key-new", "scope_id": "proj_3"},
    )
    assert saved.status_code == 200
    assert keychain.get_key(providers.OPENAI) == "test-openai-key-new"
    assert settings.get(settings.OPENAI_PROJECT) == "proj_3"


@pytest.fixture
def first(monkeypatch):
    """Runs a change as the route hands the test to the service: after the request
    arrived, before the test started."""
    real = agent_service.check_and_store_key

    def during(change):
        def check(*args, **kwargs):
            change()
            return real(*args, **kwargs)

        monkeypatch.setattr(agent_service, "check_and_store_key", check)

    return during


def test_a_key_removed_as_its_test_starts_isnt_tested(stubs, app_client, first):
    # The route read the saved key before the test's count.
    keychain.set_key("test-openai-key-old", providers.OPENAI)
    settings.put(settings.OPENAI_PROJECT, "proj_1")
    first(lambda: agent_service.remove_key(providers.OPENAI))
    late = app_client.post("/api/settings/keys/openai/test", json={})
    assert late.status_code == 422
    assert keychain.get_key(providers.OPENAI) is None
    assert settings.get(settings.OPENAI_PROJECT) is None
    assert stubs[providers.OPENAI].requests == []


def test_an_id_saved_as_a_test_starts_is_the_one_tested(stubs, app_client, first):
    keychain.set_key("test-openai-key-old", providers.OPENAI)
    settings.put(settings.OPENAI_PROJECT, "proj_1")
    first(lambda: agent_service.set_key_scope(providers.OPENAI, "proj_2"))
    tested = app_client.post("/api/settings/keys/openai/test", json={})
    assert tested.status_code == 200
    assert settings.get(settings.OPENAI_PROJECT) == "proj_2"
    (sent,) = stubs[providers.OPENAI].requests
    assert {k.casefold(): v for k, v in sent.headers.items()}[
        "openai-project"
    ] == "proj_2"


def test_the_older_route_reads_the_saved_key_as_its_test_starts(
    stubs, app_client, first
):
    keychain.set_key("sk-ant-k1", providers.ANTHROPIC)
    settings.put(settings.ANTHROPIC_WORKSPACE, "wrkspc_1")
    first(lambda: agent_service.remove_key(providers.ANTHROPIC))
    late = app_client.post("/api/settings/key/test", json={})
    assert late.status_code == 422
    assert keychain.get_key(providers.ANTHROPIC) is None
    assert settings.get(settings.ANTHROPIC_WORKSPACE) is None


def test_a_new_key_that_needed_its_id_keeps_needing_it(needs_project, app_client):
    # Saved with its ID, a new key that was refused without one lost the mark, so
    # clearing the ID left it ready.
    keychain.delete_key(providers.OPENAI)
    test = "/api/settings/keys/openai/test"
    new = {"key": "test-openai-key-needs-project"}
    assert app_client.post(test, json=new).status_code == 422
    assert keychain.get_key(providers.OPENAI) is None, "not stored until it works"
    saved = app_client.post(test, json={**new, "scope_id": "proj_right"})
    assert saved.status_code == 200
    assert needs(app_client) is False
    cleared = app_client.put("/api/settings/keys/openai/scope", json={"scope_id": ""})
    assert cleared.json()["needs_id"] is True
    assert policy.key_ready(providers.OPENAI) is False


def test_a_new_key_tested_with_an_id_it_doesnt_need_needs_none(stubs, app_client):
    test = "/api/settings/keys/openai/test"
    saved = app_client.post(
        test, json={"key": "test-openai-key-any-project", "scope_id": "proj_1"}
    )
    assert saved.status_code == 200
    app_client.put("/api/settings/keys/openai/scope", json={"scope_id": ""})
    assert needs(app_client) is False
    assert policy.key_ready(providers.OPENAI) is True


def test_each_new_key_keeps_its_own_learned_id_need(needs_project, app_client):
    # A second key's refusal overwrote the first's.
    keychain.delete_key(providers.OPENAI)
    test = "/api/settings/keys/openai/test"
    first, second = (
        {"key": "test-openai-key-needs-project-a"},
        {"key": "test-openai-key-needs-project-b"},
    )
    assert app_client.post(test, json=first).status_code == 422
    assert app_client.post(test, json=second).status_code == 422
    assert (
        app_client.post(test, json={**first, "scope_id": "proj_right"}).status_code
        == 200
    )
    app_client.put("/api/settings/keys/openai/scope", json={"scope_id": ""})
    assert needs(app_client) is True
    assert (
        app_client.post(test, json={**second, "scope_id": "proj_right"}).status_code
        == 200
    )
    app_client.put("/api/settings/keys/openai/scope", json={"scope_id": ""})
    assert needs(app_client) is True
    assert policy.key_ready(providers.OPENAI) is False
