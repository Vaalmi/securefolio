"""The calls on each provider: the key test, the Workspace and Project IDs, a refusal
for a model with no known price on every path, the local model for each job, the
Activity lines, the route's scope and thread, and no key in any record."""

import asyncio
import io
import json
import logging
import socket
from typing import cast

import httpx2
import pytest
import sqlalchemy as sa
from langchain_core.runnables import RunnableConfig
from PIL import Image

from agent import api, harness, models
from backend import (
    agent_service,
    cards,
    collections,
    connectivity,
    database,
    keychain,
    model_catalog,
    policy,
    providers,
    settings,
    sources,
    spend,
    states,
    threads,
    vault,
)
from backend.build_mode import (
    STUB_ANTHROPIC_ENV,
    STUB_OLLAMA_ENV,
    STUB_OPENAI_ENV,
    STUB_OPENROUTER_ENV,
)
from testing.ask_helpers import ask, default_reply, finish, last_answer, new_thread
from testing.pdfs import pdf_pages
from testing.queue import drain
from testing.stub_models import (
    OPENROUTER_LIST,
    answers_of,
    completion,
    ollama_reply,
    openai_reply,
    stub_anthropic,
    stub_ollama,
    stub_openai,
)
from worker import batches

UNPRICED = "openrouter:someone/no-price"


def tiny_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, "PNG")
    return buffer.getvalue()


def logged() -> list[dict]:
    with database.read() as connection:
        rows = connection.execute(sa.select(database.egress_log)).mappings().all()
    return [dict(row) for row in rows]


def header(request, name: str) -> str | None:
    """As HTTP has it, a header's name in any case."""
    found = {key.casefold(): value for key, value in request.headers.items()}
    return found.get(name.casefold())


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def anthropic(monkeypatch):
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = default_reply
        yield stub


@pytest.fixture
def openai(monkeypatch):
    with stub_openai() as stub:
        monkeypatch.setenv(STUB_OPENAI_ENV, stub.url)
        stub.reply = openai_reply
        yield stub


@pytest.fixture
def openrouter(monkeypatch):
    with stub_openai() as stub:
        monkeypatch.setenv(STUB_OPENROUTER_ENV, stub.url)
        stub.reply = openai_reply
        yield stub


@pytest.fixture
def local(monkeypatch):
    with stub_ollama() as stub:
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        yield stub


@pytest.fixture
def both(library, openai, anthropic):
    return {providers.OPENAI: openai, providers.ANTHROPIC: anthropic}


@pytest.fixture
def every(anthropic, openai, openrouter, local):
    return anthropic, openai, openrouter, local


@pytest.fixture
def keys():
    for provider in providers.PROVIDERS:
        keychain.set_key(f"test-{provider}-key", provider)
    yield
    for provider in providers.PROVIDERS:
        keychain.delete_key(provider)


@pytest.fixture
def lease(library, tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "Lease.txt").write_text("The rent is due monthly.\n")
    sources.add_source(str(folder), states.STANDARD)
    drain()


class TestTheKeyTest:
    def test_openais_sends_its_project_id_and_is_logged(self, library, openai):
        models.check_key(providers.OPENAI, "sk-proj-x", "proj_1")
        (sent,) = openai.requests
        assert header(sent, "OpenAI-Project") == "proj_1"
        # Room for its reasoning: live, 2026-11-05, GPT-6 Luna refused a reply of one
        # token ("max_tokens or model output limit was reached") and answered in 26.
        assert sent.body["max_completion_tokens"] == 64
        (row,) = logged()
        assert (row["provider"], row["purpose"], row["outcome"]) == (
            "openai",
            "key_test",
            "sent",
        )

    def test_a_refused_openai_key_is_refused(self, library, openai):
        openai.status = 401
        openai.reply = lambda _p, _b: {"error": {"message": "Incorrect API key"}}
        with pytest.raises(models.KeyRefused):
            models.check_key(providers.OPENAI, "sk-bad")

    @pytest.mark.parametrize(
        ("provider", "message"),
        [
            (
                providers.OPENAI,
                "This key needs an OpenAI-Project header naming a project.",
            ),
            (providers.ANTHROPIC, "This API key is not scoped to a workspace."),
        ],
    )
    @pytest.mark.parametrize("status", [400, 401])
    def test_a_key_that_needs_its_id_records_it_and_routes_locally(
        self, both, provider, message, status
    ):
        stub = both[provider]
        keychain.set_key("sk-saved", provider)
        stub.status = status
        stub.reply = lambda _p, _b: {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": message},
        }
        with pytest.raises(models.IdNeeded):
            agent_service.check_and_store_key("sk-saved", None, provider)
        assert policy.needs_its_id(provider) is True
        assert policy.key_ready(provider) is False
        model = "openai:gpt-6.1-sol" if provider == providers.OPENAI else None
        if model:
            settings.put(settings.MODEL_ANSWER, model)
        assert policy.route("standard").local is True
        keychain.delete_key(provider)

    def test_openrouters_is_its_key_check_with_no_model_and_no_charge(
        self, library, openrouter
    ):
        models.check_key(providers.OPENROUTER, "sk-or-x")
        (sent,) = openrouter.requests
        assert sent.path.endswith("/key")
        assert sent.headers["Authorization"] == "Bearer sk-or-x"
        # In the cloud log like every request, at no cost.
        (row,) = logged()
        assert (row["provider"], row["purpose"], row["outcome"]) == (
            "openrouter",
            "key_test",
            "sent",
        )
        assert row["cost_usd"] == 0.0
        assert row["document_ids"] == []

    def test_a_refused_openrouter_key_is_refused(self, library, openrouter):
        openrouter.status = 401
        with pytest.raises(models.KeyRefused):
            models.check_key(providers.OPENROUTER, "sk-or-bad")
        (row,) = logged()
        assert (row["outcome"], row["detail"], row["cost_usd"]) == (
            "failed",
            "HTTP 401",
            0.0,
        )

    def test_offline_tests_no_key_on_any_provider(self, library, openai, openrouter):
        settings.put(settings.OFFLINE, True)
        for provider in (providers.OPENAI, providers.OPENROUTER):
            with pytest.raises(models.CloudRefused):
                models.check_key(provider, "sk-x")
        assert openai.requests == []
        assert openrouter.requests == []


class TestTheIdsOnEveryCall:
    def test_the_project_id_goes_with_openais_answers(
        self, lease, openai, keys, app_client
    ):
        settings.put(settings.OPENAI_PROJECT, "proj_9")
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")
        ask(app_client, new_thread(app_client), "When is the rent due?")
        assert openai.requests
        assert {header(r, "OpenAI-Project") for r in openai.requests} == {"proj_9"}

    def test_the_workspace_id_goes_with_anthropics(
        self, lease, anthropic, keys, app_client
    ):
        settings.put(settings.ANTHROPIC_WORKSPACE, "wrkspc_9")
        ask(app_client, new_thread(app_client), "When is the rent due?")
        assert anthropic.requests
        assert {
            r.headers.get("anthropic-workspace-id") for r in anthropic.requests
        } == {"wrkspc_9"}

    def test_no_id_set_sends_no_header(self, lease, openai, keys, app_client):
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        ask(app_client, new_thread(app_client), "When is the rent due?")
        assert all(header(r, "OpenAI-Project") is None for r in openai.requests)


class TestNoKnownPrice:
    """Refused before anything is sent, with the reason in the egress log."""

    def refused_for_price(self) -> dict:
        (row,) = logged()
        assert row["outcome"] == "refused"
        assert "no known price for" in row["detail"].casefold()
        return row

    @pytest.mark.parametrize(
        "purpose",
        [
            policy.Purpose.ANSWER,
            policy.Purpose.REWRITE,
            policy.Purpose.SUMMARY,
            policy.Purpose.MEMORY,
            policy.Purpose.CARD,
            policy.Purpose.EVAL_JUDGE,
        ],
    )
    def test_every_chat_purpose(self, library, openrouter, keys, purpose):
        part = policy.Part(policy.PartKind.TURN, "Hello.")
        call = models.CloudCall(purpose, UNPRICED, "s", [part], lambda _p: [])
        with pytest.raises(models.CloudRefused):
            asyncio.run(models.ask_cloud(call))
        assert openrouter.requests == []
        assert self.refused_for_price()["purpose"] == purpose

    def test_the_judge(self, library, anthropic, keys, monkeypatch):
        monkeypatch.setattr(models, "JUDGE_MODEL", "anthropic:claude-retired-1")
        cited = models.Cited("T", "words", "passage", "d", states.STANDARD)
        with pytest.raises(models.CloudRefused):
            models.judge_sentence("A claim.", [cited], None)
        assert anthropic.requests == []
        assert self.refused_for_price()["purpose"] == "eval_judge"

    def test_a_batch(self, library, openrouter, anthropic, keys):
        retired = "anthropic:claude-retired-1"  # Anthropic's batch API, no price
        item = models.BatchItem("s-d", "d", "Some text.")
        with pytest.raises(models.BatchRefused):
            models.submit_batch(
                [item], {}, models.BatchOf(retired, "s"), lambda _id: True
            )
        assert anthropic.requests == []
        assert openrouter.requests == []
        assert self.refused_for_price()["purpose"] == "card"

    def test_a_page_read(self, library, tmp_path, openrouter, keys):
        folder = tmp_path / "w"
        folder.mkdir()
        (folder / "a.pdf").write_bytes(pdf_pages("One."))
        sources.add_source(str(folder), states.STANDARD)
        drain()
        with database.read() as connection:
            document_id = connection.execute(
                sa.select(database.documents.c.id)
            ).scalar_one()
        settings.put(settings.MODEL_PAGE, UNPRICED)
        with pytest.raises(models.CloudRefused, match="No known price"):
            models.read_page_in_cloud(document_id, 1, 1, tiny_png())
        assert openrouter.requests == []


class TestALocalModelPerJob:
    @pytest.fixture
    def three(self, library, local):
        tags = {
            settings.LOCAL_ANSWERS: "answers:1b",
            settings.LOCAL_CARDS: "cards:2b",
            settings.LOCAL_FIGURES: "figures:3b",
        }
        for job, tag in tags.items():
            settings.put(job, tag)

        def reply(path, body):
            if path == "/api/tags":
                return {"models": [{"name": tag} for tag in tags.values()]}
            return {
                "model": body.get("model"),
                "message": {"role": "assistant", "content": json.dumps({"a": 1})},
                "done": True,
                "done_reason": "stop",
            }

        local.reply = reply
        return tags

    def chat_models(self, local) -> list[str]:
        return [r.body["model"] for r in local.requests if r.path == "/api/chat"]

    def test_answers_use_local_answers(self, three, local):
        asyncio.run(models.ask_local("s", "q"))
        assert self.chat_models(local) == ["answers:1b"]

    def test_cards_use_local_cards(self, three, local):
        task = models.Task(policy.Purpose.CARD, "s", local=True)
        asyncio.run(models.structured(task, {"type": "object", "properties": {}}))
        assert self.chat_models(local) == ["cards:2b"]

    def test_pictures_use_local_figures(self, three, local):
        models.read_picture(tiny_png())
        assert self.chat_models(local) == ["figures:3b"]

    def test_helpers_use_local_answers(self, three, local):
        task = models.Task(policy.Purpose.REWRITE, "s", local=True)
        asyncio.run(models.structured(task, {"type": "object", "properties": {}}))
        assert self.chat_models(local) == ["answers:1b"]


class TestTheActivity:
    def test_a_cloud_answer_names_cloud_ai_and_the_model(
        self, lease, openai, keys, app_client
    ):
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        found = ask(app_client, new_thread(app_client), "When is the rent due?")
        answer = found["messages"][-1]["answer"]
        assert (answer["model"], answer["local"]) == ("openai:gpt-6.1-sol", False)
        assert any("Cloud AI, GPT-6.1 Sol" in line for line in answer["activity"])
        assert answer["model_name"] == "GPT-6.1 Sol", "as the answer shows it"

    def test_a_fallback_names_local_ai_its_tag_and_why(
        self, lease, openai, local, app_client
    ):
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        found = ask(app_client, new_thread(app_client), "When is the rent due?")
        answer = found["messages"][-1]["answer"]
        assert answer["local"] is True
        joined = " ".join(answer["activity"])
        assert "Local AI" in joined
        assert "gemma4:26b" in joined
        assert "needs an OpenAI key" in joined
        assert openai.requests == []
        assert answer["model_name"] == "gemma4:26b"

    def test_the_route_says_when_it_is_local_for_want_of_a_key(
        self, lease, openai, local, monkeypatch, app_client
    ):
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        route = app_client.get("/api/ask/route").json()
        assert (route["local"], route["needs_key"]) == (True, True)
        assert "needs an OpenAI key" in route["reason"]
        keychain.set_key("test-openai-key", providers.OPENAI)
        monkeypatch.setattr(connectivity, "reachable", lambda *_given: False)
        route = app_client.get("/api/ask/route").json()
        assert (route["local"], route["needs_key"]) == (True, False), "no connection"
        keychain.delete_key(providers.OPENAI)


class TestTheRoute:
    @pytest.fixture
    def mixed(self, library, tmp_path, keys, anthropic):
        ids = {}
        for name, level in (("plain", states.STANDARD), ("hidden", states.PRIVATE)):
            folder = tmp_path / name
            folder.mkdir()
            (folder / f"{name}.txt").write_text(f"The {name} note.\n")
            sources.add_source(str(folder), level)
        drain()
        with database.read() as connection:
            for row in connection.execute(
                sa.select(database.documents.c.id, database.documents.c.title)
            ):
                ids[row.title] = row.id
        return ids

    def test_a_selection_holding_a_private_document_answers_locally(
        self, mixed, app_client
    ):
        route = app_client.get(
            "/api/ask/route", params={"documents": [mixed["hidden"], mixed["plain"]]}
        ).json()
        assert route["local"] is True
        standard = app_client.get(
            "/api/ask/route", params={"documents": [mixed["plain"]]}
        ).json()
        assert standard["local"] is False

    def test_a_collection_holding_a_private_document_answers_locally(
        self, mixed, app_client
    ):
        made = collections.create("Mixed")
        collections.add_documents(made["id"], [mixed["hidden"]])
        route = app_client.get(
            "/api/ask/route", params={"collection": made["id"]}
        ).json()
        assert route["local"] is True

    def test_a_thread_that_touched_private_answers_locally(self, mixed, app_client):
        thread = new_thread(app_client)
        threads.taint(thread)
        route = app_client.get("/api/ask/route", params={"thread": thread}).json()
        assert route["local"] is True

    def test_a_blocked_question_is_refused_with_its_reason(self, mixed, app_client):
        settings.put(settings.MODEL_ANSWER, UNPRICED)
        thread = new_thread(app_client)
        made = app_client.post(
            f"/api/threads/{thread}/messages",
            json={"text": "When?", "mode": "standard"},
        )
        assert made.status_code == 409
        assert "No known price" in made.json()["detail"]


class TestConnectivity:
    def test_each_provider_is_checked_at_its_own_host(
        self, library, openai, monkeypatch
    ):
        connectivity.forget()
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, f"http://127.0.0.1:{free_port()}")
        assert connectivity.reachable(providers.OPENAI) is True
        assert connectivity.reachable(providers.ANTHROPIC) is False
        connectivity.forget()


class TestNoKeyInAnyRecord:
    def test_a_planted_key_is_in_no_log_record_or_body(
        self, lease, every, app_client, caplog
    ):
        anthropic, openai, openrouter, local = every
        planted = {p: f"sk-planted-{p}-7Qx2" for p in providers.PROVIDERS}
        for provider, key in planted.items():
            keychain.set_key(key, provider)
        model_catalog.store_openrouter(
            [row for row in map(model_catalog.to_row, OPENROUTER_LIST) if row]
        )
        caplog.set_level(logging.DEBUG)
        for model in (
            "anthropic:claude-sonnet-5-5",
            "openai:gpt-6.1-sol",
            "openrouter:anthropic/claude-sonnet-5-5",
        ):
            settings.put(settings.MODEL_ANSWER, model)
            ask(app_client, new_thread(app_client), "When is the rent due?")
        models.check_key(providers.OPENAI, planted[providers.OPENAI])
        models.check_key(providers.OPENROUTER, planted[providers.OPENROUTER])
        assert answers_of(openai)
        assert answers_of(openrouter)
        records = json.dumps(logged(), default=str) + caplog.text
        bodies = "".join(s.all_text() for s in (anthropic, openai, openrouter, local))
        files = b"".join(
            path.read_bytes() for path in vault.root().rglob("*") if path.is_file()
        )
        for key in planted.values():
            assert key not in records
            assert key not in bodies
            assert key.encode() not in files
        assert keychain.hint(providers.OPENAI) == "••••7Qx2"
        for provider in providers.PROVIDERS:
            keychain.delete_key(provider)


class TestTheCardEstimate:
    def source_id(self) -> str:
        with database.read() as connection:
            return connection.execute(
                sa.select(database.sources.c.id).where(
                    database.sources.c.name == "plain"
                )
            ).scalar_one()

    def test_it_names_the_helper_model_and_prices_from_it(self, lease, app_client):
        url = f"/api/sources/{self.source_id()}/organize-estimate"
        shown = app_client.get(url).json()
        assert shown["model"] == "anthropic:claude-haiku-5-5"
        assert shown["backfill_usd"] > 0
        assert shown["batched"] is False

    def test_an_unpriced_helper_has_no_price_to_show(self, lease, app_client):
        settings.put(settings.MODEL_HELPER, UNPRICED)
        url = f"/api/sources/{self.source_id()}/organize-estimate"
        shown = app_client.get(url).json()
        assert (shown["backfill_usd"], shown["per_document_usd"]) == (None, None)
        assert shown["model"] == UNPRICED


HELPERS = {
    providers.OPENAI: "openai:gpt-6-luna",
    providers.OPENROUTER: "openrouter:openai/gpt-oss-120b",
}
SCHEMA = {
    "type": "object",
    "title": "found",
    "properties": {"actions": {"type": "array", "items": {"type": "string"}}},
    "required": ["actions"],
    "additionalProperties": False,
}


@pytest.fixture(params=[providers.OPENAI, providers.OPENROUTER])
def helper_on(request, library, openai, openrouter, keys):
    """The helper model on OpenAI or OpenRouter, and that provider's stub."""
    model_catalog.store_openrouter(
        [row for row in map(model_catalog.to_row, OPENROUTER_LIST) if row]
    )
    settings.put(settings.MODEL_HELPER, HELPERS[request.param])
    return {providers.OPENAI: openai, providers.OPENROUTER: openrouter}[request.param]


class TestEachKindOfCallOnEachProvider:
    @pytest.mark.parametrize("purpose", [policy.Purpose.MEMORY, policy.Purpose.CARD])
    def test_a_structured_helper_goes_with_its_schema(self, helper_on, purpose):
        helper_on.reply = lambda _p, body: (
            openai_reply(_p, body)
            | {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"actions": ["a"]}',
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        )
        part = policy.Part(policy.PartKind.TURN, "I prefer short answers.")
        task = models.Task(purpose, "Reply with JSON.", [part])
        found = asyncio.run(models.structured(task, SCHEMA, fall_back=False))
        assert found == {"actions": ["a"]}
        (sent,) = helper_on.requests
        assert sent.body["response_format"]["type"] == "json_schema"
        assert "provider" not in sent.body
        (row,) = logged()
        assert (row["purpose"], row["model"]) == (
            purpose,
            settings.get(settings.MODEL_HELPER),
        )

    def test_a_summary_goes_as_prose(self, helper_on):
        part = policy.Part(policy.PartKind.TURN, "We talked about the lease.")
        task = models.Task(
            policy.Purpose.SUMMARY, "Keep what a later follow-up needs.", [part]
        )
        assert asyncio.run(models.helper_text(task)) == "A short summary."
        (sent,) = helper_on.requests
        assert "response_format" not in sent.body

    def test_a_page_goes_to_openrouters_claude_as_an_image(
        self, library, tmp_path, openrouter, keys
    ):
        model_catalog.store_openrouter(
            [row for row in map(model_catalog.to_row, OPENROUTER_LIST) if row]
        )
        folder = tmp_path / "w"
        folder.mkdir()
        (folder / "a.pdf").write_bytes(pdf_pages("One."))
        sources.add_source(str(folder), states.STANDARD)
        drain()
        with database.read() as connection:
            document_id = connection.execute(
                sa.select(database.documents.c.id)
            ).scalar_one()
        settings.put(settings.MODEL_PAGE, "openrouter:anthropic/claude-sonnet-5-5")
        models.read_page_in_cloud(document_id, 1, 1, tiny_png())
        (sent,) = openrouter.requests
        blocks = sent.body["messages"][-1]["content"]
        assert [b["type"] for b in blocks].count("image_url") == 1
        (row,) = logged()
        assert (row["provider"], row["outcome"]) == ("openrouter", "sent")


def run_steps(run_id: str) -> list[dict]:
    """The steps a run showed, as the app reads them."""
    with database.read() as connection:
        rows = connection.execute(
            sa.select(database.run_events.c.data).where(
                database.run_events.c.run_id == run_id,
                database.run_events.c.event == "step",
            )
        ).scalars()
        return list(rows)


class TestTheHelperRoute:
    def test_an_answer_falling_back_keeps_a_ready_helper_in_the_cloud(
        self, lease, openai, anthropic, local, app_client
    ):
        # The answer's provider has no key, the helper's has one.
        keychain.set_key("test-anthropic-key", providers.ANTHROPIC)
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        thread = new_thread(app_client)
        ask(app_client, thread, "When is the rent due?")
        # A follow-up is rewritten to stand alone: the helper's step.
        found = ask(app_client, thread, "And how much is it?")
        answer = found["messages"][-1]["answer"]
        assert answer["local"] is True
        # So it isn't shown as free.
        assert answer["helper_local"] is False
        assert openai.requests == []
        assert anthropic.requests, "the helper ran on this Mac, not on Anthropic"
        keychain.delete_key(providers.ANTHROPIC)

    def test_a_private_answer_keeps_its_helpers_on_the_mac(
        self, lease, anthropic, local, keys, app_client
    ):
        thread = new_thread(app_client)
        ask(app_client, thread, "When is the rent due?", "private")
        found = ask(app_client, thread, "And how much is it?", "private")
        answer = found["messages"][-1]["answer"]
        assert (answer["local"], answer["helper_local"]) == (True, True)
        assert anthropic.requests == []

    def test_the_route_names_a_cloud_helper_beside_a_local_answer(
        self, lease, openai, anthropic, local, app_client
    ):
        # The chat's note says what still leaves the Mac.
        keychain.set_key("test-anthropic-key", providers.ANTHROPIC)
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        route = app_client.get("/api/ask/route").json()
        assert (route["local"], route["helper_local"]) == (True, False)
        assert route["helper_name"] == "Claude Haiku 5.5"
        keychain.delete_key(providers.ANTHROPIC)


class TestOllamaNotRunning:
    def test_a_question_only_local_ai_can_answer_is_refused(
        self, lease, monkeypatch, app_client
    ):
        # No key anywhere, and nothing listening where Ollama should be.
        monkeypatch.setenv(STUB_OLLAMA_ENV, f"http://127.0.0.1:{free_port()}")
        monkeypatch.setattr(agent_service, "OLLAMA_FRESH_SECONDS", 0.0)
        route = app_client.get("/api/ask/route").json()
        assert route["blocked"]
        assert "Ollama" in route["blocked"]
        made = app_client.post(
            f"/api/threads/{new_thread(app_client)}/messages",
            json={"text": "When is the rent due?", "mode": "standard"},
        )
        assert made.status_code == 409
        assert "Ollama" in made.json()["detail"]


class TestTheDeepConsent:
    def test_a_deep_model_changed_while_waiting_doesnt_change_what_goes(
        self, lease, openai, anthropic, keys, app_client
    ):
        # The consent priced one model; the run must not send another.
        # A question keeps the model its route chose: a change applies to the next.
        settings.put(settings.ASK_FIRST, 0.0)
        thread = new_thread(app_client)
        found = ask(app_client, thread, "When is the rent due?", "deep")
        card = found["active_run"]["card"]
        assert card["model"] == "anthropic:claude-opus-5-5"
        app_client.patch("/api/settings", json={"model_deep": "openai:gpt-6.1-sol"})
        run = app_client.post(
            f"/api/threads/{thread}/resume",
            json={"card_id": card["id"], "choice": "deep"},
        ).json()
        done = finish(app_client, run)
        assert answers_of(openai) == [], "sent to a model nobody agreed to"
        assert done["last_failure"] is None
        assert done["messages"][-1]["answer"]["model"] == "anthropic:claude-opus-5-5"


class TestTheWritingStep:
    def test_it_names_cloud_ai_and_the_model(self, lease, openai, keys, app_client):
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        run = app_client.post(
            f"/api/threads/{new_thread(app_client)}/messages",
            json={"text": "When is the rent due?", "mode": "standard"},
        ).json()
        finish(app_client, run)
        (writing,) = [s for s in run_steps(run["id"]) if s["name"] == "writing"]
        assert writing["detail"] == "Cloud AI, GPT-6.1 Sol"

    def test_a_fallback_names_local_ai_its_tag_and_why(
        self, lease, openai, local, app_client
    ):
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        run = app_client.post(
            f"/api/threads/{new_thread(app_client)}/messages",
            json={"text": "When is the rent due?", "mode": "standard"},
        ).json()
        finish(app_client, run)
        (writing,) = [s for s in run_steps(run["id"]) if s["name"] == "writing"]
        assert writing["detail"].startswith("Local AI, gemma4:26b")
        assert "needs an OpenAI key" in writing["detail"]


class TestRoutesAndCards:
    def test_standard_on_a_deep_card_takes_the_standard_route(
        self, lease, anthropic, local, keys, app_client
    ):
        # Answers is set to the local model, Deep to a ready cloud one.
        settings.put(settings.MODEL_ANSWER, settings.LOCAL)
        settings.put(settings.ASK_FIRST, 0.0)
        thread = new_thread(app_client)
        found = ask(app_client, thread, "When is the rent due?", "deep")
        card = found["active_run"]["card"]
        assert card["kind"] == "deep_cost"
        run = app_client.post(
            f"/api/threads/{thread}/resume",
            json={"card_id": card["id"], "choice": "standard"},
        ).json()
        done = finish(app_client, run)
        assert done["last_failure"] is None
        answer = done["messages"][-1]["answer"]
        assert answer["local"] is True
        assert answers_of(anthropic) == []

    def test_the_route_uses_local_model_in_development_as_answering_does(
        self, library, local, monkeypatch, app_client
    ):
        # LOCAL_MODEL stands for all three local jobs.
        monkeypatch.setenv("LOCAL_MODEL", "qwen3.5:9b")
        local.reply = lambda path, body: (
            {"models": [{"name": "qwen3.5:9b"}]}
            if path == "/api/tags"
            else {"model": "qwen3.5:9b", "message": {"content": "x"}, "done": True}
        )
        route = app_client.get("/api/ask/route", params={"mode": "private"}).json()
        assert route["blocked"] is None
        assert route["model"] == "qwen3.5:9b"


OR_SONNET = "openrouter:anthropic/claude-sonnet-5-5"
OR_SONNET_PRICE = (2.0, 10.0)  # per million, from the stub list


def refresh_without(model_id: str) -> None:
    """A refresh of OpenRouter's list that no longer has this model, after the owner
    chose another: its row goes."""
    gone = model_id.split(":", 1)[1]
    model_catalog.store_openrouter(
        [
            row
            for row in map(model_catalog.to_row, OPENROUTER_LIST)
            if row and row["id"] != gone
        ]
    )


def refresh_at(model_id: str, price: tuple[float, float]) -> None:
    """A refresh that lists this model at a new price."""
    name = model_id.split(":", 1)[1]
    listed = []
    for entry in OPENROUTER_LIST:
        row = model_catalog.to_row(entry)
        if row and row["id"] == name:
            row = {**row, "input_usd": price[0], "output_usd": price[1]}
        if row:
            listed.append(row)
    model_catalog.store_openrouter(listed)


def refresh_unpriced(model_id: str) -> None:
    """A refresh that lists this model with its price missing, or set per request."""
    name = model_id.split(":", 1)[1]
    listed = []
    for entry in OPENROUTER_LIST:
        row = model_catalog.to_row(entry)
        if row and row["id"] == name:
            row = {**row, "input_usd": None, "output_usd": None}
        if row:
            listed.append(row)
    model_catalog.store_openrouter(listed)


def answer_rows() -> list[dict]:
    return [row for row in logged() if row["purpose"] == "answer"]


@pytest.fixture
def listed(library):
    refresh_at(OR_SONNET, OR_SONNET_PRICE)


class TestOnePricePerCall:
    """The price is read once, at the gate, and a refresh at any moment after that
    changes neither what's recorded nor what it costs."""

    @pytest.fixture
    def on_openrouter(self, lease, listed, anthropic, keys):
        """An answer on OpenRouter's Claude, its helper on Anthropic."""

    def ask_on_openrouter(self, app_client) -> dict:
        settings.put(settings.MODEL_ANSWER, OR_SONNET)
        return ask(app_client, new_thread(app_client), "When is the rent due?")

    def settled_at_the_gates_price(self) -> None:
        (row,) = answer_rows()
        assert row["outcome"] == "sent"
        assert (row["input_usd"], row["output_usd"]) == OR_SONNET_PRICE
        assert row["cost_usd"] == pytest.approx(
            (row["input_tokens"] * 2.0 + row["output_tokens"] * 10.0) / 1_000_000
        )

    def test_a_refresh_before_the_attempt_is_recorded(
        self, on_openrouter, openrouter, app_client, monkeypatch
    ):
        real = models.api.log_attempt

        def refreshed_first(*given, **named):
            settings.put(settings.MODEL_ANSWER, "anthropic:claude-sonnet-5-5")
            refresh_without(OR_SONNET)
            return real(*given, **named)

        monkeypatch.setattr(models.api, "log_attempt", refreshed_first)
        found = self.ask_on_openrouter(app_client)
        assert found["last_failure"] is None
        self.settled_at_the_gates_price()

    def test_a_refresh_while_the_reply_is_on_its_way(
        self, on_openrouter, openrouter, app_client
    ):
        def refreshed_meanwhile(path, body):
            if "<source id=" in json.dumps(body):  # the answer itself
                settings.put(settings.MODEL_ANSWER, "anthropic:claude-sonnet-5-5")
                refresh_at(OR_SONNET, (9.0, 90.0))
            return openai_reply(path, body)

        openrouter.reply = refreshed_meanwhile
        found = self.ask_on_openrouter(app_client)
        assert found["last_failure"] is None
        self.settled_at_the_gates_price()

    def test_a_call_given_its_price_goes_at_it_whatever_the_list_says(
        self, library, listed, openrouter, keys
    ):
        refresh_at(OR_SONNET, (9.0, 90.0))
        part = policy.Part(policy.PartKind.TURN, "Hello.")
        call = models.CloudCall(
            policy.Purpose.ANSWER,
            OR_SONNET,
            "s",
            [part],
            lambda parts: [{"role": "user", "content": parts[0].text}],
            price=OR_SONNET_PRICE,
        )
        asyncio.run(models.ask_cloud(call))
        (row,) = answer_rows()
        assert (row["input_usd"], row["output_usd"]) == OR_SONNET_PRICE


class TestTheDeepConsentRecord:
    """The owner's answer is read from the run's record, and it counts only for the
    model, price and estimate its card showed."""

    @pytest.fixture
    def deep_on_openrouter(self, lease, listed, openrouter, anthropic, keys):
        settings.put(settings.MODEL_DEEP, OR_SONNET)
        settings.put(settings.ASK_FIRST, 0.0)

    def card_of(self, app_client, thread) -> dict:
        found = ask(app_client, thread, "When is the rent due?", "deep")
        card = found["active_run"]["card"]
        assert card["kind"] == "deep_cost"
        return card

    def answer_card(self, app_client, thread, card, choice) -> dict:
        run = app_client.post(
            f"/api/threads/{thread}/resume",
            json={"card_id": card["id"], "choice": choice},
        ).json()
        return finish(app_client, run)

    def test_a_price_raised_while_the_card_waits_asks_again(
        self, deep_on_openrouter, openrouter, app_client
    ):
        thread = new_thread(app_client)
        card = self.card_of(app_client, thread)
        assert card["price"] == list(OR_SONNET_PRICE)
        refresh_at(OR_SONNET, (4.0, 20.0))
        again = self.answer_card(app_client, thread, card, "deep")
        assert answers_of(openrouter) == [], "sent at a price nobody saw"
        fresh = again["active_run"]["card"]
        assert fresh["price"] == [4.0, 20.0]
        assert fresh["estimate_usd"] >= card["estimate_usd"]
        done = self.answer_card(app_client, thread, fresh, "deep")
        assert done["last_failure"] is None
        (row,) = answer_rows()
        assert (row["input_usd"], row["output_usd"]) == (4.0, 20.0)

    def test_a_deep_yes_goes_at_the_price_its_card_showed(
        self, deep_on_openrouter, openrouter, app_client
    ):
        thread = new_thread(app_client)
        card = self.card_of(app_client, thread)
        done = self.answer_card(app_client, thread, card, "deep")
        assert done["messages"][-1]["answer"]["model"] == OR_SONNET
        (row,) = answer_rows()
        assert [row["input_usd"], row["output_usd"]] == card["price"]

    def test_standard_stands_when_deep_drops_under_the_threshold(
        self, deep_on_openrouter, openrouter, local, app_client
    ):
        settings.put(settings.MODEL_ANSWER, settings.LOCAL)
        thread = new_thread(app_client)
        card = self.card_of(app_client, thread)
        # Cheaper now, or the owner raised the threshold: no card would be asked.
        settings.put(settings.MODEL_DEEP, "openai:gpt-6-luna")
        settings.put(settings.ASK_FIRST, 100.0)
        done = self.answer_card(app_client, thread, card, "standard")
        assert done["last_failure"] is None
        answer = done["messages"][-1]["answer"]
        assert answer["local"] is True
        assert answers_of(openrouter) == []


class TestRoutedAndAgreed:
    """What goes is what was routed and agreed to, the owner's Standard is read first,
    the threshold compares the exact estimate, and a backlog goes by a batch only where
    the batch API is built."""

    @pytest.fixture
    def deep_on_openrouter(self, lease, listed, openrouter, anthropic, keys):
        settings.put(settings.MODEL_DEEP, OR_SONNET)
        settings.put(settings.ASK_FIRST, 0.0)

    def test_deep_changed_after_the_yes_isnt_what_goes(
        self, deep_on_openrouter, openrouter, openai, app_client, monkeypatch
    ):
        # Settings changes between the consent and the call.
        real = harness._send_cloud  # noqa: SLF001

        async def changed_first(*given, **named):
            # From a thread, as the app writes: on the event loop it would wait on the
            # checkpointer's transaction.
            await asyncio.to_thread(
                settings.put, settings.MODEL_DEEP, "openai:gpt-6-astra"
            )
            return await real(*given, **named)

        monkeypatch.setattr(harness, "_send_cloud", changed_first)
        thread = new_thread(app_client)
        found = ask(app_client, thread, "When is the rent due?", "deep")
        card = found["active_run"]["card"]
        run = app_client.post(
            f"/api/threads/{thread}/resume",
            json={"card_id": card["id"], "choice": "deep"},
        ).json()
        done = finish(app_client, run)
        assert answers_of(openai) == [], "sent to a model nobody agreed to"
        assert done["messages"][-1]["answer"]["model"] == OR_SONNET
        (row,) = answer_rows()
        assert row["model"] == OR_SONNET
        assert [row["input_usd"], row["output_usd"]] == card["price"]

    def test_standard_stands_when_deeps_price_goes_missing(
        self, deep_on_openrouter, openrouter, local, app_client
    ):
        # A refresh drops the Deep model's price while the card waits.
        settings.put(settings.MODEL_ANSWER, settings.LOCAL)
        thread = new_thread(app_client)
        found = ask(app_client, thread, "When is the rent due?", "deep")
        card = found["active_run"]["card"]
        refresh_unpriced(OR_SONNET)
        assert model_catalog.price(OR_SONNET) is None
        run = app_client.post(
            f"/api/threads/{thread}/resume",
            json={"card_id": card["id"], "choice": "standard"},
        ).json()
        done = finish(app_client, run)
        assert done["last_failure"] is None
        assert done["messages"][-1]["answer"]["local"] is True
        assert answers_of(openrouter) == []

    def test_an_estimate_a_fraction_over_the_threshold_asks(
        self, deep_on_openrouter, openrouter, app_client, monkeypatch
    ):
        # $0.504 is over $0.50, though it shows as $0.50.
        settings.put(settings.ASK_FIRST, 0.50)
        monkeypatch.setattr(harness, "_deep_estimate", lambda *_given: 0.504)
        thread = new_thread(app_client)
        found = ask(app_client, thread, "When is the rent due?", "deep")
        card = found["active_run"]["card"]
        assert card["kind"] == "deep_cost"
        assert card["estimate_usd"] == 0.50
        assert answers_of(openrouter) == []


def batch_requests(stub) -> list:
    return [r for r in stub.requests if "/batches" in r.path]


class TestNoBatchButAnthropics:
    """A backlog never reaches a provider other than its helper's. OpenAI has its own
    batch API (worker/tests/test_openai_batches.py); OpenRouter has none, so its
    backlog is written one card at a time, and never reaches Anthropic."""

    @pytest.fixture
    def opted_in_backlog(self, library, tmp_path):
        root = tmp_path / "backlog"
        root.mkdir()
        for n in range(spend.BATCH_ABOVE + 1):
            (root / f"Doc {n}.txt").write_text(f"Document {n}.\n")
        return root

    @pytest.mark.parametrize("helper", ["openrouter:anthropic/claude-sonnet-5-5"])
    def test_a_backlog_goes_one_card_at_a_time(
        self, opted_in_backlog, listed, every, keys, helper
    ):
        anthropic, openai, openrouter, _local = every
        settings.put(settings.MODEL_HELPER, helper)
        source = sources.add_source(
            str(opted_in_backlog), states.STANDARD, auto_organize=True
        )
        drain()
        batches.tick()
        assert anthropic.requests == [], "the helper's documents went to Anthropic"
        assert batch_requests(openai) == []
        chosen = openai if helper.startswith("openai:") else openrouter
        assert len(chosen.requests) >= spend.BATCH_ABOVE + 1
        assert sources.organize_estimate(source["id"])["batched"] is False

    def test_a_batch_for_another_provider_is_never_sent(self, library, anthropic, keys):
        item = models.BatchItem("s-d", "d", "Some text.")
        batch = models.BatchOf("openrouter:anthropic/claude-sonnet-5-5", "s")
        with pytest.raises(models.BatchRefused):
            models.submit_batch([item], {}, batch, lambda _id: True)
        assert anthropic.requests == []


LEASE_CARD = {
    "title": "Lease",
    "summary": "A lease for unit 3B.",
    "doc_type": "Contract",
    "dates": [],
    "people": [],
    "organizations": [],
    "amounts": ["$2,350.00"],
    "tags": ["home"],
}


def card_reply(path: str, body: dict) -> dict:
    """OpenAI's side: a card in JSON for the card prompt, anything else as usual."""
    if "card" in json.dumps(body.get("response_format") or {}):
        return completion(body, json.dumps(LEASE_CARD))
    return openai_reply(path, body)


def only_card() -> dict:
    with database.read() as connection:
        document_id = connection.execute(
            sa.select(database.documents.c.id)
        ).scalar_one()
    return cards.get(document_id)


class TestDeepPricing:
    """The Deep card prices the whole request, and a card names the model that wrote
    it."""

    @pytest.fixture
    def deep_on_openrouter(self, lease, listed, openrouter, anthropic, keys):
        settings.put(settings.MODEL_DEEP, OR_SONNET)
        settings.put(settings.ASK_FIRST, 0.0)

    def test_the_deep_card_prices_the_question(self, deep_on_openrouter, app_client):
        # A long question must raise the estimate by what it costs to send.
        short = "When is the rent due?"
        long = short + " Please be precise." * 4000
        shown = [
            ask(app_client, new_thread(app_client), question, "deep")["active_run"][
                "card"
            ]["estimate_usd"]
            for question in (short, long)
        ]
        extra = models.api.count_tokens(long) - models.api.count_tokens(short)
        costs = extra * OR_SONNET_PRICE[0] / 1_000_000
        assert costs > 0.02
        assert shown[1] - shown[0] >= costs - 0.01  # each shown to the cent

    @pytest.mark.parametrize("provider", ["anthropic", "openai"])
    def test_a_connection_that_never_opened_is_known_on_either_sdk(self, provider):
        # Both SDKs send through httpx2 now, and LangChain wraps OpenAI's error again.
        dead = f"http://127.0.0.1:{free_port()}"
        if provider == "anthropic":
            client = models.anthropic.Anthropic(
                api_key="k", base_url=dead, max_retries=0
            )
            send = lambda: client.messages.create(  # noqa: E731
                model="x", max_tokens=1, messages=[{"role": "user", "content": "hi"}]
            )
        else:
            from langchain_openai import ChatOpenAI  # noqa: PLC0415
            from pydantic import SecretStr  # noqa: PLC0415

            chat = ChatOpenAI(
                model="x", api_key=SecretStr("k"), base_url=f"{dead}/v1", max_retries=0
            )
            send = lambda: chat.invoke("hi")  # noqa: E731
        with pytest.raises(Exception) as failed:  # noqa: PT011
            send()
        assert models._never_connected(failed.value)  # noqa: SLF001

    def test_an_openai_card_names_its_model(self, library, tmp_path, openai, keys):
        # Not "Claude Haiku 5.5".
        settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")
        openai.reply = card_reply
        folder = tmp_path / "w"
        folder.mkdir()
        (folder / "Lease.txt").write_text("The rent is due monthly.\n")
        sources.add_source(str(folder), states.STANDARD, auto_organize=True)
        drain()
        card = only_card()
        assert card["written_by"] == "GPT-6 Luna"
        assert card["title"] == "Lease"

    def test_a_card_written_locally_after_a_cut_names_the_local_model(
        self, library, tmp_path, local, keys, monkeypatch
    ):
        # Nothing listens where OpenAI should be, so the card is written here.
        settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")
        monkeypatch.setenv(STUB_OPENAI_ENV, f"http://127.0.0.1:{free_port()}")
        local.reply = lambda path, body: (
            {"models": [{"name": "gemma4:26b"}]}
            if path == "/api/tags"
            else {
                "model": body.get("model", "stub"),
                "message": {"role": "assistant", "content": json.dumps(LEASE_CARD)},
                "done": True,
                "done_reason": "stop",
            }
        )
        folder = tmp_path / "w"
        folder.mkdir()
        (folder / "Lease.txt").write_text("The rent is due monthly.\n")
        sources.add_source(str(folder), states.STANDARD, auto_organize=True)
        drain()
        card = only_card()
        assert card["written_by"] != "GPT-6 Luna"
        assert card["written_by"] == "gemma4:26b"  # the local model for cards


class TestAttemptPricing:
    """An attempt is priced from what it sends. An answer's parts carry only each
    passage's id, and its render puts the passage in."""

    @pytest.fixture
    def long_lease(self, library, tmp_path, keys):
        """A lease long enough that its passages outweigh the instructions."""
        folder = tmp_path / "long"
        folder.mkdir()
        clauses = " ".join(
            f"Clause {n}: the tenant pays the rent of unit {n} monthly, by transfer."
            for n in range(400)
        )
        (folder / "Lease.txt").write_text(clauses + "\n")
        sources.add_source(str(folder), states.STANDARD)
        drain()

    def test_an_answer_with_no_usage_stays_at_an_estimate_that_counts_its_evidence(
        self, long_lease, anthropic, app_client, monkeypatch
    ):
        seen: list[models.CloudCall] = []
        real = models._begin  # noqa: SLF001

        def kept(call: models.CloudCall):
            seen.append(call)
            return real(call)

        monkeypatch.setattr(models, "_begin", kept)
        # The stream ends with no usage, so the attempt keeps its estimate.
        monkeypatch.setattr(models.api, "log_sent", lambda *_a, **_k: 0.0)
        ask(app_client, new_thread(app_client), "When is the rent due?")
        (row,) = answer_rows()
        assert row["outcome"] == "attempted"
        (call,) = [c for c in seen if c.purpose == policy.Purpose.ANSWER]
        (sent,) = [r for r in anthropic.requests if "Clause" in r.raw]
        price = (row["input_usd"], row["output_usd"])
        evidence = models.api.count_tokens(json.dumps(sent.body["messages"]))
        system = models.api.count_tokens(call.system)
        assert evidence > 2 * system  # so the ids alone couldn't reach the bound
        input_share = row["cost_usd"] - spend.cost_at(price, 0, call.max_tokens)
        assert input_share >= spend.cost_at(price, int(evidence * 0.8), 0)


def timed_out(cause: Exception) -> Exception:
    """OpenAI's timeout as its SDK raises it, with httpx2's cause under it: the SDKs
    send through httpx2."""
    failed = models.openai.APITimeoutError(
        request=httpx2.Request("POST", "http://127.0.0.1/v1/chat/completions")
    )
    failed.__cause__ = cause
    return failed


class TestConnectionTimeouts:
    """A connection not made in time never opened, so nothing went; a reply not read
    in time may have been sent."""

    def test_a_connect_timeout_never_opened_and_a_read_timeout_may_have_sent(self):
        assert models._never_connected(timed_out(httpx2.ConnectTimeout("t")))  # noqa: SLF001
        assert not models._never_connected(timed_out(httpx2.ReadTimeout("t")))  # noqa: SLF001

    @pytest.fixture
    def on_openai(self, lease, openai, local, keys):
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")

    def test_an_answer_whose_connection_times_out_is_written_locally(
        self, on_openai, local, app_client, monkeypatch
    ):
        real = models._stream  # noqa: SLF001

        async def cut(built, *given, **named):
            # Only the cloud call: the local answer streams as it would.
            if type(built).__name__ != "ChatOllama":
                raise timed_out(httpx2.ConnectTimeout("t"))
            return await real(built, *given, **named)

        monkeypatch.setattr(models, "_stream", cut)
        local.reply = ollama_reply
        found = ask(app_client, new_thread(app_client), "When is the rent due?")
        assert last_answer(found)["local"] is True
        (row,) = answer_rows()
        assert (row["outcome"], row["detail"], row["cost_usd"]) == (
            "failed",
            "no connection",
            0.0,
        )

    @pytest.mark.parametrize(
        ("cause", "outcome", "charged"),
        [
            (httpx2.ConnectTimeout("t"), "failed", False),
            (httpx2.ReadTimeout("t"), "attempted", True),
        ],
    )
    def test_a_key_test_keeps_its_estimate_unless_nothing_went(
        self, library, monkeypatch, cause, outcome, charged
    ):
        def cut(*_given, **_named):
            raise timed_out(cause)

        monkeypatch.setattr(models, "_openai_key_test", cut)
        with pytest.raises(models.NoConnection):
            models.check_key(providers.OPENAI, "sk-x")
        (row,) = logged()
        assert row["outcome"] == outcome
        assert (row["cost_usd"] > 0) is charged


class TestNoAmbientProject:
    """OpenAI's SDK reads OPENAI_PROJECT_ID when given no project. SecureFolio sends the
    saved Project ID, or none, as it was tested."""

    @pytest.fixture(autouse=True)
    def ambient(self, monkeypatch):
        monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-ambient")

    def test_a_key_test_with_no_id_sends_none(self, library, openai):
        models.check_key(providers.OPENAI, "test-openai-key")
        (sent,) = openai.requests
        assert header(sent, "OpenAI-Project") is None

    def test_answers_go_with_the_saved_id_or_none(
        self, lease, openai, keys, app_client
    ):
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        ask(app_client, new_thread(app_client), "When is the rent due?")
        assert openai.requests
        assert {header(r, "OpenAI-Project") for r in openai.requests} == {None}
        openai.requests.clear()
        settings.put(settings.OPENAI_PROJECT, "proj_9")
        ask(app_client, new_thread(app_client), "When is the rent due?")
        assert {header(r, "OpenAI-Project") for r in openai.requests} == {"proj_9"}

    def test_openrouter_gets_no_openai_project(
        self, lease, listed, openrouter, keys, app_client
    ):
        settings.put(settings.MODEL_ANSWER, OR_SONNET)
        ask(app_client, new_thread(app_client), "When is the rent due?")
        assert answers_of(openrouter)
        assert all(header(r, "OpenAI-Project") is None for r in openrouter.requests)

    def test_a_batch_read_sends_the_saved_id_only(self, library, openai, keys):
        made = models._openai_batches(None)  # noqa: SLF001
        assert made.project is None


class TestNoAmbientAddress:
    """The SDKs read OPENAI_BASE_URL and ANTHROPIC_BASE_URL when given no address, so
    a variable left from another project could send key tests, answers and batches
    elsewhere. Each client goes to the provider's own address, named in
    backend/providers.py, or its stub."""

    ELSEWHERE = "http://127.0.0.1:19999/v1"

    @pytest.fixture(autouse=True)
    def ambient(self, monkeypatch):
        for provider in (providers.OPENAI, providers.ANTHROPIC):
            monkeypatch.delenv(providers.PROVIDERS[provider].stub_env, raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", self.ELSEWHERE)
        monkeypatch.setenv("ANTHROPIC_BASE_URL", self.ELSEWHERE)
        monkeypatch.setenv("OPENAI_API_BASE", self.ELSEWHERE)
        monkeypatch.setenv("ANTHROPIC_API_URL", self.ELSEWHERE)

    def test_openais_clients_go_to_openai(self, library, keys):
        here = "https://api.openai.com/v1/"
        with models._openai_client("test-openai-key", None, None) as made:  # noqa: SLF001
            assert str(made.base_url) == here
        assert str(models._openai_batches(None).base_url) == here  # noqa: SLF001
        built = models.build_cloud("openai:gpt-6.1-sol", 10, lambda: None)
        assert str(built.root_client.base_url) == here
        assert str(built.root_async_client.base_url) == here

    def test_anthropics_clients_go_to_anthropic(self, library, keys):
        here = "https://api.anthropic.com"
        assert str(models._sdk().base_url) == here  # noqa: SLF001
        built = models.build_cloud("anthropic:claude-sonnet-5-5", 10, lambda: None)
        assert built.anthropic_api_url == here
        assert str(built._client.base_url) == here  # noqa: SLF001

    def test_anthropics_key_test_goes_to_anthropic(self, library, monkeypatch):
        made: list[str] = []

        class Sent(Exception):
            pass

        class Client:
            def __init__(self, **fields):
                made.append(str(fields.get("base_url")))
                raise Sent

        monkeypatch.setattr(models.anthropic, "Client", Client)
        with pytest.raises(Sent):
            models._anthropic_key_test(  # noqa: SLF001
                "anthropic:claude-haiku-5-5", "sk-ant-test", None, lambda: None
            )
        assert made == ["https://api.anthropic.com"]


class TestNoAmbientWorkspace:
    """ANTHROPIC_WORKSPACE_ID from the environment was sent with every
    Anthropic call and key test. Only the evaluation, whose key comes from .env too,
    takes it."""

    @pytest.fixture(autouse=True)
    def ambient(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_ambient")

    def test_a_key_test_with_no_id_sends_none(self, library, anthropic):
        models.check_key(providers.ANTHROPIC, "sk-ant-test")
        (sent,) = anthropic.requests
        assert header(sent, "anthropic-workspace-id") is None

    def test_answers_go_with_the_saved_id_or_none(
        self, lease, anthropic, keys, app_client
    ):
        ask(app_client, new_thread(app_client), "When is the rent due?")
        assert anthropic.requests
        assert {header(r, "anthropic-workspace-id") for r in anthropic.requests} == {
            None
        }

    def test_the_evaluation_with_keys_from_env_takes_it(
        self, library, anthropic, monkeypatch
    ):
        monkeypatch.setitem(keychain._from_environment, "on", True)  # noqa: SLF001
        fields = models._workspace_fields()  # noqa: SLF001
        assert fields["default_headers"]["anthropic-workspace-id"] == "wrkspc_ambient"


class TestARunKeepsWhatItRouted:
    """The helper model and the local model were read again
    after routing, so a change in Settings during a run sent its later work to
    another model."""

    def prepared(self, app_client, monkeypatch, change) -> dict:
        monkeypatch.setattr(harness, "get_stream_writer", lambda: lambda _event: None)
        real_recall = harness.memory.recall

        def recall(*given, **named):
            change()  # Settings change while the run recalls its memories
            return real_recall(*given, **named)

        monkeypatch.setattr(harness.memory, "recall", recall)
        config: RunnableConfig = {
            "configurable": {"thread_id": new_thread(app_client), "run_id": "r"}
        }
        state = cast(
            "harness.AskState",
            {"question": "When is the rent due?", "mode": "standard"},
        )
        return asyncio.run(harness.prepare(state, config))

    def test_the_local_model_is_the_one_routed(
        self, lease, keys, app_client, monkeypatch
    ):
        settings.put(settings.LOCAL_ANSWERS, "gemma4:26b")
        found = self.prepared(
            app_client,
            monkeypatch,
            lambda: settings.put(settings.LOCAL_ANSWERS, "other:1b"),
        )
        assert found["local_tag"] == "gemma4:26b"

    def test_the_helper_model_is_the_one_routed(
        self, lease, keys, app_client, monkeypatch
    ):
        settings.put(settings.MODEL_HELPER, "anthropic:claude-haiku-5-5")
        found = self.prepared(
            app_client,
            monkeypatch,
            lambda: settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna"),
        )
        assert found["helper_model"] == "anthropic:claude-haiku-5-5"

    def test_a_summary_goes_to_the_helper_it_was_given(
        self, library, anthropic, openai, keys
    ):
        settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")
        task = models.Task(
            api.Purpose.SUMMARY,
            "Summarize.",
            [api.Part(api.PartKind.TURN, "We spoke about the rent.")],
            model="anthropic:claude-haiku-5-5",
        )
        asyncio.run(models.helper_text(task))
        assert anthropic.requests
        assert openai.requests == []


class TestRoutingOffTheLoop:
    """Routing may wait on the network, so a message routes off the event loop, and a
    Private question asks nothing of the network."""

    def test_a_private_question_doesnt_ask_whether_a_provider_is_reachable(
        self, library, keys, monkeypatch
    ):
        asked: list[str] = []
        monkeypatch.setattr(
            policy.connectivity, "reachable", lambda *given: asked.append(given[0])
        )
        assert policy.route("private").local is True
        assert policy.route("standard", include_private=True).local is True
        assert asked == []

    def test_a_standard_question_starts_without_asking_after_deeps_provider(
        self, lease, openai, keys, app_client, monkeypatch
    ):
        # Deep's provider, timing out, held up a Standard answer's start by a second.
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")
        settings.put(settings.MODEL_DEEP, "anthropic:claude-opus-5-5")
        asked: list[str] = []
        before_start: list[list[str]] = []
        real_reachable = policy.connectivity.reachable

        def reachable(provider=providers.ANTHROPIC, wait=True):
            asked.append(provider)
            return real_reachable(provider, wait)

        monkeypatch.setattr(policy.connectivity, "reachable", reachable)
        real_start = agent_service.Service.start

        async def start(self, *given, **named):
            before_start.append(list(asked))
            return await real_start(self, *given, **named)

        monkeypatch.setattr(agent_service.Service, "start", start)
        found = ask(app_client, new_thread(app_client), "When is the rent due?")
        assert found["messages"][-1]["answer"]["model"] == "openai:gpt-6.1-sol"
        assert before_start
        assert providers.OPENAI in before_start[0]
        # Nor while the run prepares, routing again.
        assert providers.ANTHROPIC not in asked

    def test_no_connection_is_tried_before_the_run_starts(
        self, lease, openai, keys, app_client, monkeypatch
    ):
        # A timing-out connection to the answer's own provider held the first step
        # back by a second.
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")
        policy.connectivity.forget()
        tried: list[tuple[str, int]] = []
        real_connects = policy.connectivity._connects  # noqa: SLF001

        def connects(host, port):
            tried.append((host, port))
            return real_connects(host, port)

        monkeypatch.setattr(policy.connectivity, "_connects", connects)
        before_start: list[int] = []
        real_start = agent_service.Service.start

        async def start(self, *given, **named):
            before_start.append(len(tried))
            return await real_start(self, *given, **named)

        monkeypatch.setattr(agent_service.Service, "start", start)
        found = ask(app_client, new_thread(app_client), "When is the rent due?")
        assert before_start == [0]
        assert tried, "the run asks, waiting, once it has started"
        assert found["messages"][-1]["answer"]["model"] == "openai:gpt-6.1-sol"

    def test_a_cloud_question_doesnt_wait_on_ollama(
        self, lease, openai, keys, app_client, monkeypatch
    ):
        # A question answered and helped in the cloud waited first
        # for Ollama's model list.
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")
        refreshed: list[bool] = []
        real = agent_service.refresh_ollama
        monkeypatch.setattr(
            agent_service,
            "refresh_ollama",
            lambda: (refreshed.append(True), real())[1],
        )
        made = app_client.post(
            f"/api/threads/{new_thread(app_client)}/messages",
            json={"text": "When is the rent due?", "mode": "standard"},
        )
        assert made.status_code == 200
        assert refreshed == []

    def test_a_question_nothing_can_answer_is_still_refused_at_once(
        self, lease, monkeypatch, app_client
    ):
        # No key anywhere, and no Ollama: refused before the run, with no network.
        monkeypatch.setenv(STUB_OLLAMA_ENV, f"http://127.0.0.1:{free_port()}")
        monkeypatch.setattr(agent_service, "OLLAMA_FRESH_SECONDS", 0.0)
        made = app_client.post(
            f"/api/threads/{new_thread(app_client)}/messages",
            json={"text": "When is the rent due?", "mode": "standard"},
        )
        assert made.status_code == 409

    def test_the_ask_box_still_learns_whether_deep_goes_to_the_cloud(
        self, lease, keys, app_client
    ):
        settings.put(settings.MODEL_DEEP, "anthropic:claude-opus-5-5")
        assert app_client.get("/api/ask/route").json()["deep_cloud"] is True

    def test_a_message_is_routed_off_the_event_loop(
        self, lease, local, keys, app_client, monkeypatch
    ):
        real = policy.route
        on_loop: list[bool] = []

        def spy(*given, **named):
            try:
                asyncio.get_running_loop()
                on_loop.append(True)
            except RuntimeError:
                on_loop.append(False)
            return real(*given, **named)

        monkeypatch.setattr(policy, "route", spy)
        local.reply = ollama_reply
        thread = new_thread(app_client)
        app_client.post(
            f"/api/threads/{thread}/messages",
            json={"text": "When is the rent due?", "mode": "private"},
        )
        assert on_loop
        assert on_loop[0] is False
