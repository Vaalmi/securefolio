"""Settings, Models for each task: each task's list by provider with prices and "not
measured", which providers have a ready key, and a chosen model OpenRouter's newest
list left out."""

import pytest

from backend import keychain, model_catalog, providers, settings
from testing.stub_models import OPENROUTER_LIST

SONNET = "anthropic:claude-sonnet-5-5"
GPT_OSS = "openrouter:openai/gpt-oss-120b"


@pytest.fixture
def no_keys(library):
    yield
    for provider in providers.PROVIDERS:
        keychain.delete_key(provider)


def tasks(app_client) -> tuple[dict[str, dict], dict[str, bool]]:
    found = app_client.get("/api/models/tasks").json()
    ready = {row["id"]: row["key_ready"] for row in found["providers"]}
    return {row["task"]: row for row in found["tasks"]}, ready


def ids(task: dict) -> set[str]:
    return {model["id"] for model in task["models"]}


def test_each_task_lists_what_it_can_do_with_its_price(no_keys, app_client):
    model_catalog.store_openrouter(
        [row for found in OPENROUTER_LIST if (row := model_catalog.to_row(found))]
    )
    found, _ = tasks(app_client)
    assert set(found) == {"answer", "deep", "helper", "page"}
    assert {SONNET, "openai:gpt-6.1-sol", GPT_OSS} <= ids(found["answer"])
    assert GPT_OSS not in ids(found["page"]), "it takes no images"
    assert "openrouter:anthropic/claude-sonnet-5-5" in ids(found["page"])
    assert "openrouter:openrouter/auto" not in ids(found["answer"]), "no known price"
    sonnet = next(m for m in found["answer"]["models"] if m["id"] == SONNET)
    assert (sonnet["provider"], sonnet["input_usd"], sonnet["output_usd"]) == (
        "anthropic",
        2.0,
        10.0,
    )
    assert sonnet["measured"] is True
    luna = next(m for m in found["answer"]["models"] if m["id"] == "openai:gpt-6-luna")
    assert luna["measured"] is False
    assert found["answer"]["chosen"] == SONNET
    assert (found["answer"]["may_run_locally"], found["deep"]["may_run_locally"]) == (
        True,
        False,
    )


def test_a_provider_counts_as_ready_only_with_its_key(no_keys, app_client):
    _, ready = tasks(app_client)
    assert ready == {"anthropic": False, "openai": False, "openrouter": False}
    keychain.set_key("sk-proj-k1", providers.OPENAI)
    _, ready = tasks(app_client)
    assert ready["openai"] is True
    assert ready["anthropic"] is False
    settings.put(settings.OPENAI_NEEDS_ID, True)  # its test found it needs its ID
    found = app_client.get("/api/models/tasks").json()["providers"]
    openai = next(row for row in found if row["id"] == "openai")
    assert (openai["key_ready"], openai["needs_id"], openai["id_label"]) == (
        False,
        True,
        "Project ID",
    )


def test_a_chosen_model_no_longer_listed_says_so(no_keys, app_client):
    rows = [row for found in OPENROUTER_LIST if (row := model_catalog.to_row(found))]
    model_catalog.store_openrouter(rows)
    settings.put(settings.MODEL_ANSWER, GPT_OSS)
    model_catalog.store_openrouter(
        [row for row in rows if row["id"] != providers.split(GPT_OSS)[1]]
    )
    found, _ = tasks(app_client)
    gone = next(m for m in found["answer"]["models"] if m["id"] == GPT_OSS)
    assert gone["listed"] is False
    assert gone["input_usd"] == pytest.approx(0.1), "its last price"
    assert GPT_OSS not in ids(found["deep"]), "offered only where it's chosen"
