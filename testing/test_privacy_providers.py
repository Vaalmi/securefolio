"""What reaches OpenAI and OpenRouter, read from the raw bodies their stubs record.
Private-derived text never does, on any path, whichever provider a task is set to.
The planted strings are made per test, so a leak can't hide by being a common word.

Cards and batches on OpenAI and OpenRouter have tests of their own; here their purpose
is refused at the gate on each provider, as every purpose is."""

import asyncio
import io
import re
import uuid

import pytest
import sqlalchemy as sa
from PIL import Image

from agent import memory, models
from backend import (
    database,
    documents,
    keychain,
    memories,
    model_catalog,
    model_store,
    policy,
    providers,
    settings,
    sources,
    spend,
    states,
    threads,
)
from backend.build_mode import (
    STUB_ANTHROPIC_ENV,
    STUB_OLLAMA_ENV,
    STUB_OPENAI_ENV,
    STUB_OPENROUTER_ENV,
)
from testing.ask_helpers import ask, default_reply, finish, new_thread
from testing.network import attempts
from testing.pdfs import pdf_pages
from testing.queue import drain
from testing.stub_models import (
    OPENROUTER_LIST,
    answers_of,
    openai_reply,
    stub_anthropic,
    stub_ollama,
    stub_openai,
    system_of,
)

ANSWERS = {
    providers.OPENAI: ("openai:gpt-6.1-sol", "openai:gpt-6-luna"),
    providers.OPENROUTER: (
        "openrouter:anthropic/claude-sonnet-5-5",
        "openrouter:openai/gpt-oss-120b",
    ),
}
STUB_ENVS = {
    providers.OPENAI: STUB_OPENAI_ENV,
    providers.OPENROUTER: STUB_OPENROUTER_ENV,
}
SOURCE = re.compile(r'<source id="\d+" title="[^"]*">(.*?)</source>', re.DOTALL)


def planted() -> str:
    return f"zq{uuid.uuid4().hex[:12]}"


def tiny_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def local(monkeypatch):
    with stub_ollama() as stub:
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        yield stub


@pytest.fixture
def anthropic(monkeypatch):
    keychain.set_key("test-anthropic-key", providers.ANTHROPIC)
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = default_reply
        yield stub
    keychain.delete_key(providers.ANTHROPIC)


@pytest.fixture
def stubs(library, monkeypatch):
    """OpenAI's and OpenRouter's stubs, each with its key; OpenRouter's list stored."""
    with stub_openai() as openai, stub_openai() as openrouter:
        found = {providers.OPENAI: openai, providers.OPENROUTER: openrouter}
        for provider, stub in found.items():
            keychain.set_key(f"test-{provider}-key", provider)
            monkeypatch.setenv(STUB_ENVS[provider], stub.url)
            stub.reply = openai_reply
        model_catalog.store_openrouter(
            [row for row in map(model_catalog.to_row, OPENROUTER_LIST) if row]
        )
        yield found
    for provider in found:
        keychain.delete_key(provider)


@pytest.fixture(params=[providers.OPENAI, providers.OPENROUTER])
def cloud(request, stubs):
    """The Answer, Deep, helper and page models all on one provider."""
    answer, helper = ANSWERS[request.param]
    settings.put(settings.MODEL_ANSWER, answer)
    settings.put(settings.MODEL_DEEP, answer)
    settings.put(settings.MODEL_HELPER, helper)
    if request.param == providers.OPENAI:
        settings.put(settings.MODEL_PAGE, answer)
    return stubs[request.param]


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


def source_named(name: str) -> str:
    with database.read() as connection:
        return connection.execute(
            sa.select(database.sources.c.id).where(database.sources.c.name == name)
        ).scalar_one()


def logged() -> list[dict]:
    with database.read() as connection:
        rows = connection.execute(sa.select(database.egress_log)).mappings().all()
    return [dict(row) for row in rows]


class TestEveryPath:
    def test_the_question_its_rewrite_and_the_summary(self, worlds, cloud, app_client):
        thread = new_thread(app_client)
        for text in ("When is the rent due?", "And the second one?", "And then?"):
            ask(app_client, thread, text)
        assert answers_of(cloud), "the answers went to this provider"
        assert any("stands alone" in system_of(r.body) for r in cloud.requests)
        assert worlds not in cloud.all_text()
        assert "Secret note" not in cloud.all_text()

    def test_a_private_scope_makes_no_cloud_call(
        self, worlds, cloud, local, app_client
    ):
        scope = {"kind": "library", "include_private": True, "label": "All"}
        thread = new_thread(app_client, scope)
        ask(app_client, thread, "When is the rent due?")
        assert cloud.requests == []

    def test_a_thread_that_touched_private_earlier_stays_local(
        self, worlds, cloud, local, app_client
    ):
        thread = new_thread(app_client)
        threads.taint(thread)
        found = ask(app_client, thread, "When is the rent due?")
        assert cloud.requests == []
        assert found["messages"][-1]["answer"]["local"] is True

    def test_a_document_that_went_private_while_the_call_waited(
        self, worlds, cloud, app_client
    ):
        def turn_private_on_plan(path, body):
            if "stands alone" in system_of(body):
                sources.set_sensitivity(source_named("plain"), states.PRIVATE)
            return openai_reply(path, body)

        thread = new_thread(app_client)
        ask(app_client, thread, "When is the rent due?")
        cloud.requests.clear()
        cloud.reply = turn_private_on_plan
        ask(app_client, thread, "What is it about the second lease?")
        assert answers_of(cloud) == []

    def test_a_private_memory_never_goes(self, worlds, cloud, app_client):
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
        assert answers_of(cloud)
        assert secret not in cloud.all_text()

    def test_memory_extraction_on_a_private_thread_stays_on_this_mac(
        self, library, cloud, local
    ):
        thread = threads.create(
            {"kind": "library", "include_private": True, "label": "All"}, "standard"
        )
        threads.start_run(thread["id"], f"My code is {planted()}.", None)
        asyncio.run(memory.extract(thread["id"]))
        assert cloud.requests == []

    def test_offline_tries_no_connection_to_any_provider(
        self, worlds, cloud, local, app_client
    ):
        settings.put(settings.OFFLINE, True)
        thread = new_thread(app_client)
        found = ask(app_client, thread, "When is the rent due?")
        assert cloud.requests == []
        assert attempts == []
        assert found["messages"][-1]["answer"]["local"] is True


class TestTheGate:
    @pytest.mark.parametrize(
        "purpose",
        [
            policy.Purpose.ANSWER,
            policy.Purpose.REWRITE,
            policy.Purpose.SUMMARY,
            policy.Purpose.MEMORY,
            policy.Purpose.CARD,
        ],
    )
    def test_a_private_part_is_refused_on_every_purpose(self, library, cloud, purpose):
        model = str(settings.get(settings.MODEL_ANSWER))
        part = policy.Part(
            policy.PartKind.PASSAGE, planted(), sensitivity=states.PRIVATE
        )
        call = models.CloudCall(purpose, model, "s", [part], lambda _p: [])
        with pytest.raises(models.CloudRefused):
            asyncio.run(models.ask_cloud(call))
        assert cloud.requests == []
        (row,) = logged()
        assert (row["outcome"], row["purpose"]) == ("refused", purpose)
        assert (row["provider"], row["model"]) == (providers.split(model)[0], model)


class TestThePageImage:
    @pytest.fixture
    def page_doc(self, library, tmp_path):
        folder = tmp_path / "w"
        folder.mkdir()
        (folder / "a.pdf").write_bytes(pdf_pages("One.", "Two."))
        sources.add_source(str(folder), states.STANDARD)
        drain()
        with database.read() as connection:
            return connection.execute(sa.select(database.documents.c.id)).scalar_one()

    def test_the_page_goes_to_openai_as_one_image(self, page_doc, stubs):
        settings.put(settings.MODEL_PAGE, "openai:gpt-6.1-sol")
        openai = stubs[providers.OPENAI]
        models.read_page_in_cloud(page_doc, 1, 1, tiny_png())
        (sent,) = openai.requests
        assert "tools" not in sent.body
        blocks = sent.body["messages"][-1]["content"]
        assert [b["type"] for b in blocks].count("image_url") == 1
        (row,) = logged()
        assert (row["provider"], row["outcome"]) == ("openai", "sent")

    def test_a_private_page_never_goes(self, page_doc, stubs):
        settings.put(settings.MODEL_PAGE, "openai:gpt-6.1-sol")
        source_id = documents.locations(page_doc)[0]["source_id"]
        sources.set_sensitivity(source_id, states.PRIVATE)
        with pytest.raises(models.CloudRefused):
            models.read_page_in_cloud(page_doc, 1, 1, tiny_png())
        assert stubs[providers.OPENAI].requests == []


class TestLocalMeansThisMac:
    def test_a_model_on_ollamas_servers_is_never_local(self, library, monkeypatch):
        monkeypatch.setattr(model_store, "_ollama", {"at": None, "models": []})
        listed = [
            models._listed({"name": "gpt-oss:120b-cloud"}),  # noqa: SLF001
            models._listed({"name": "big:cloud"}),  # noqa: SLF001
            models._listed({"name": "gemma4:26b", "remote_host": "ollama.com"}),  # noqa: SLF001
        ]
        assert all(model["remote"] for model in listed)
        model_store.remember_ollama(listed)
        settings.put(settings.LOCAL_ANSWERS, "gemma4:26b")
        assert model_store.installed("gemma4:26b") is False
        settings.put(settings.OFFLINE, True)
        assert policy.route("standard").blocked

    @pytest.mark.parametrize(
        "tag", ["gpt-oss:120b-cloud", "kimi:cloud", "openai:gpt-6.1-sol", "local"]
    )
    @pytest.mark.parametrize("job", ["local_answers", "local_cards", "local_figures"])
    def test_a_cloud_model_in_a_local_jobs_setting_is_refused(
        self, library, app_client, tag, job
    ):
        before = settings.get(job)
        assert app_client.patch("/api/settings", json={job: tag}).status_code == 422
        assert settings.get(job) == before

    def test_ollamas_own_remote_mark_is_refused(self, library, app_client, monkeypatch):
        monkeypatch.setattr(model_store, "_ollama", {"at": None, "models": []})
        model_store.remember_ollama([{"name": "plain:7b", "remote": True}])
        made = app_client.patch("/api/settings", json={"local_answers": "plain:7b"})
        assert made.status_code == 422


class TestTheSameParts:
    def test_every_provider_gets_the_same_parts_in_its_own_wrapping(
        self, worlds, stubs, anthropic, app_client
    ):
        sent: dict[str, list[str]] = {}
        for provider, model in (
            (providers.ANTHROPIC, "anthropic:claude-sonnet-5-5"),
            (providers.OPENAI, "openai:gpt-6.1-sol"),
            (providers.OPENROUTER, "openrouter:anthropic/claude-sonnet-5-5"),
        ):
            settings.put(settings.MODEL_ANSWER, model)
            ask(app_client, new_thread(app_client), "When is the rent due?")
            stub = anthropic if provider == providers.ANTHROPIC else stubs[provider]
            if provider == providers.ANTHROPIC:
                (body,) = [r.body for r in stub.requests if "search_result" in r.raw]
                blocks = body["messages"][-1]["content"]
                passages = [
                    b["content"][0]["text"]
                    for b in blocks
                    if b["type"] == "search_result"
                ]
                question = blocks[-1]["text"]
            else:
                (body,) = [r.body for r in answers_of(stub)]
                content = body["messages"][-1]["content"]
                passages = SOURCE.findall(content)
                question = content.rsplit("\n", 1)[-1]
            sent[provider] = [*passages, question]
        assert sent[providers.ANTHROPIC] == sent[providers.OPENAI]
        assert sent[providers.OPENAI] == sent[providers.OPENROUTER]
        assert sent[providers.OPENAI][-1] == "When is the rent due?"

    def test_openrouter_carries_no_host_preference(self, worlds, stubs, app_client):
        settings.put(settings.MODEL_ANSWER, ANSWERS[providers.OPENROUTER][0])
        settings.put(settings.MODEL_HELPER, ANSWERS[providers.OPENROUTER][1])
        thread = new_thread(app_client)
        ask(app_client, thread, "When is the rent due?")
        ask(app_client, thread, "And the second one?")
        bodies = [r.body for r in stubs[providers.OPENROUTER].requests]
        assert bodies
        assert all("provider" not in body for body in bodies)

    def test_openrouters_claude_caches_its_prefix_and_openai_needs_nothing(
        self, worlds, stubs, app_client
    ):
        for model in ("openrouter:anthropic/claude-sonnet-5-5", "openai:gpt-6.1-sol"):
            settings.put(settings.MODEL_ANSWER, model)
            ask(app_client, new_thread(app_client), "When is the rent due?")
        (routed,) = answers_of(stubs[providers.OPENROUTER])
        (direct,) = answers_of(stubs[providers.OPENAI])
        assert "cache_control" in routed.raw
        assert "cache_control" not in direct.raw


class TestTheLogAndTheCap:
    def test_one_row_per_call_with_its_provider_and_model(
        self, worlds, stubs, anthropic, app_client
    ):
        settings.put(settings.MODEL_HELPER, ANSWERS[providers.OPENAI][1])
        settings.put(settings.MODEL_ANSWER, ANSWERS[providers.OPENROUTER][0])
        thread = new_thread(app_client)
        ask(app_client, thread, "When is the rent due?")
        ask(app_client, thread, "And the second one?")
        rows = logged()
        sent = sum(len(s.requests) for s in (*stubs.values(), anthropic))
        assert len(rows) == sent
        assert {(r["provider"], r["model"]) for r in rows} == {
            ("openai", ANSWERS[providers.OPENAI][1]),
            ("openrouter", ANSWERS[providers.OPENROUTER][0]),
        }
        assert all(r["cost_usd"] > 0 for r in rows)

    def test_the_months_spend_adds_up_every_provider(
        self, worlds, stubs, anthropic, app_client
    ):
        for model in (
            "anthropic:claude-sonnet-5-5",
            "openai:gpt-6.1-sol",
            "openrouter:anthropic/claude-sonnet-5-5",
        ):
            settings.put(settings.MODEL_ANSWER, model)
            ask(app_client, new_thread(app_client), "When is the rent due?")
        rows = logged()
        assert {r["provider"] for r in rows} == {"anthropic", "openai", "openrouter"}
        assert spend.spent_this_month() == pytest.approx(
            sum(r["cost_usd"] for r in rows)
        )

    @pytest.mark.parametrize("where", ["under", "at", "over"])
    def test_background_work_stops_at_the_cap_across_providers(
        self, worlds, stubs, anthropic, app_client, where
    ):
        """Background cloud work stops when the month's spend reaches the cap. Spent on
        Anthropic and OpenAI, it stops a memory pass on OpenRouter."""
        for model in ("anthropic:claude-sonnet-5-5", "openai:gpt-6.1-sol"):
            settings.put(settings.MODEL_ANSWER, model)
            ask(app_client, new_thread(app_client), "When is the rent due?")
        spent = spend.spent_this_month()
        assert {r["provider"] for r in logged()} == {"anthropic", "openai"}
        settings.put(
            settings.SPEND_CAP,
            {"under": spent * 10, "at": spent, "over": spent / 2}[where],
        )
        model = ANSWERS[providers.OPENROUTER][1]
        part = policy.Part(policy.PartKind.TURN, "I prefer short answers.")
        call = models.CloudCall(
            policy.Purpose.MEMORY, model, "s", [part], lambda _p: []
        )
        if where == "under":
            asyncio.run(models.ask_cloud(call))
            assert len(stubs[providers.OPENROUTER].requests) == 1
        else:
            with pytest.raises(models.CloudRefused, match="spending cap"):
                asyncio.run(models.ask_cloud(call))
            assert stubs[providers.OPENROUTER].requests == []
            assert logged()[-1]["outcome"] == "refused"
            assert logged()[-1]["provider"] == "openrouter"

    def test_a_deep_run_on_openai_asks_first_priced_from_its_model(
        self, worlds, cloud, app_client
    ):
        settings.put(settings.ASK_FIRST, 0.0)
        thread = new_thread(app_client)
        found = ask(app_client, thread, "When is the rent due?", "deep")
        card = found["active_run"]["card"]
        model = str(settings.get(settings.MODEL_DEEP))
        assert card["kind"] == "deep_cost"
        assert card["model"] == model
        found = model_catalog.get(model)
        assert found is not None
        assert card["model_name"] == found.name
        assert answers_of(cloud) == []
        run = app_client.post(
            f"/api/threads/{thread}/resume",
            json={"card_id": card["id"], "choice": "deep"},
        ).json()
        done = finish(app_client, run)
        assert done["messages"][-1]["answer"]["model"] == model
