"""What reaches the cloud through Ask, helpers and cards, read from the raw bodies. The
planted strings are made per test, so a leak can't hide by being a common word."""

import asyncio
import json
import uuid

import pytest
import sqlalchemy as sa

from agent import memory, models
from backend import (
    database,
    keychain,
    memories,
    policy,
    settings,
    sources,
    states,
    threads,
)
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OLLAMA_ENV
from testing.ask_helpers import (
    ask,
    default_reply,
    message,
    new_thread,
    plan_reply,
    system_of,
)
from testing.network import attempts
from testing.queue import drain
from testing.stub_models import stub_anthropic, stub_ollama


def planted() -> str:
    return f"zq{uuid.uuid4().hex[:12]}"


@pytest.fixture
def cloud(monkeypatch):
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = default_reply
        yield stub
    keychain.delete_key()


@pytest.fixture
def local(monkeypatch):
    with stub_ollama() as stub:
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        yield stub


@pytest.fixture
def worlds(library, tmp_path):
    """A Standard and a Private folder, with a marker in the Private one."""
    secret = planted()
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "Lease.txt").write_text("The rent is due monthly.\n")
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "Secret note.txt").write_text(f"The rent {secret} is private.\n")
    sources.add_source(str(plain), states.STANDARD)
    sources.add_source(str(hidden), states.PRIVATE)
    drain()
    return secret


def test_private_text_stays_out_of_every_path(worlds, cloud, app_client):
    thread = new_thread(app_client)
    ask(app_client, thread, "When is the rent due?")
    ask(app_client, thread, "And the second one?")
    assert worlds not in cloud.all_text()
    assert "Secret note" not in cloud.all_text()


def test_near_miss_titles_on_a_retry_leave_private_out(
    worlds, cloud, app_client, monkeypatch
):
    def weak(_path, body):
        if "stands alone" in system_of(body):
            return plan_reply(body, "nothing like this at all")
        return message(body, [{"type": "text", "text": "No."}])

    cloud.reply = weak
    thread = new_thread(app_client)
    ask(app_client, thread, "Secret note please look now")
    plans = [r for r in cloud.requests if "stands alone" in system_of(r.body)]
    assert plans
    joined = " ".join(r.raw for r in plans).replace("Secret note please look now", "")
    assert "Secret note" not in joined


def test_a_private_thread_makes_no_cloud_call(worlds, cloud, local, app_client):
    scope = {"kind": "library", "include_private": True, "label": "All"}
    thread = new_thread(app_client, scope)
    ask(app_client, thread, "When is the rent due?")
    assert cloud.requests == []


def test_a_document_that_went_private_during_a_run_isnt_sent(worlds, cloud, app_client):
    def turn_private_on_plan(_path, body):
        if "stands alone" in system_of(body):
            with database.read() as connection:
                source_id = connection.execute(
                    sa.select(database.sources.c.id).where(
                        database.sources.c.name == "plain"
                    )
                ).scalar_one()
            sources.set_sensitivity(source_id, states.PRIVATE)
            return plan_reply(body)
        return default_reply(_path, body)

    thread = new_thread(app_client)
    ask(app_client, thread, "When is the rent due?")
    cloud.requests.clear()
    cloud.reply = turn_private_on_plan
    ask(app_client, thread, "What is it about the second lease?")
    answers = [r for r in cloud.requests if "search_result" in r.raw]
    assert answers == []


def test_offline_makes_no_attempted_connection(worlds, cloud, local, app_client):
    settings.put(settings.OFFLINE, True)
    thread = new_thread(app_client)
    found = ask(app_client, thread, "When is the rent due?")
    assert cloud.requests == []
    assert attempts == []
    assert found["messages"][-1]["answer"]["local"] is True


def test_a_refused_call_is_logged_and_never_reaches_the_stub(worlds, cloud):
    part = policy.Part(policy.PartKind.PASSAGE, "x", sensitivity=states.PRIVATE)
    call = models.CloudCall(
        policy.Purpose.ANSWER, "anthropic:claude-sonnet-5-5", "s", [part], lambda _p: []
    )
    with pytest.raises(models.CloudRefused):
        asyncio.run(models.ask_cloud(call))
    assert cloud.requests == []
    with database.read() as connection:
        row = connection.execute(sa.select(database.egress_log)).mappings().one()
    assert row["outcome"] == "refused"
    assert row["purpose"] == "answer"


def test_every_cloud_call_has_one_log_row_with_its_cost(worlds, cloud, app_client):
    thread = new_thread(app_client)
    ask(app_client, thread, "What about the second one?")
    with database.read() as connection:
        rows = connection.execute(sa.select(database.egress_log)).mappings().all()
    assert len(rows) == len(cloud.requests)
    assert {r["outcome"] for r in rows} == {"sent"}
    assert all(r["cost_usd"] > 0 for r in rows)
    assert {r["purpose"] for r in rows} <= set(policy.Purpose)


def test_the_cloud_purposes_are_the_specs_whole_list():
    assert set(policy.Purpose) == {
        "answer",
        "rewrite",
        "summary",
        "memory",
        "card",
        "key_test",
        "page_read",
        "eval_judge",
    }


def test_a_private_memory_never_reaches_a_cloud_request(worlds, cloud, app_client):
    secret = planted()
    memories.add(
        {
            "kind": "fact",
            "text": f"My rent code is {secret}.",
            "embedding": None,
            "sensitivity": states.PRIVATE,
            "source_label": "A Private chat",
        }
    )
    thread = new_thread(app_client)
    ask(app_client, thread, "What is my rent code?")
    assert secret not in cloud.all_text()


class TestMemoryPaths:
    def test_extraction_on_a_private_thread_stays_on_this_mac(
        self, library, cloud, local
    ):
        thread = threads.create(
            {"kind": "library", "include_private": True, "label": "All"}, "standard"
        )
        threads.start_run(thread["id"], "I prefer short answers.", None)
        local.reply = local_memory_reply([])
        asyncio.run(memory.extract(thread["id"]))
        assert cloud.requests == []
        assert any("/api/chat" in r.path for r in local.requests)


def local_memory_reply(actions: list[dict]):
    def reply(path: str, body: dict) -> dict:
        if path == "/api/tags":
            return {"models": [{"name": "gemma4:26b"}]}
        return {
            "model": body.get("model", "stub"),
            "message": {
                "role": "assistant",
                "content": json.dumps({"actions": actions}),
            },
            "done": True,
            "done_reason": "stop",
        }

    return reply
