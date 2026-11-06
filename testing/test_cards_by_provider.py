"""Cards on every provider: OpenRouter one card at a time, the local model standing in
for a helper with no key, a card held pending for a helper with no known price, and
each card naming what wrote it."""

import itertools
import json

import pytest
import sqlalchemy as sa

from backend import (
    cards,
    database,
    keychain,
    model_catalog,
    providers,
    refusals,
    settings,
    sources,
    spend,
    states,
)
from backend.build_mode import STUB_OLLAMA_ENV, STUB_OPENAI_ENV, STUB_OPENROUTER_ENV
from backend.tokens import count
from testing.queue import drain
from testing.stub_models import (
    OPENROUTER_LIST,
    completion,
    openai_reply,
    stub_ollama,
    stub_openai,
)
from worker import organize

GPT_OSS = "openrouter:openai/gpt-oss-120b"  # listed at $0.10 and $0.50 a million
UNPRICED = "openrouter:someone/no-price"
LUNA = "openai:gpt-6-luna"
LOCAL_TAG = "gemma4:26b"
CARD = {
    "title": "Lease",
    "summary": "A lease for unit 3B.",
    "doc_type": "Contract",
    "dates": [],
    "people": [],
    "organizations": [],
    "amounts": [],
    "tags": ["home"],
}


def billed(path: str, body: dict) -> dict:
    """OpenAI's side, billing each card request at its real size."""
    if "card" not in json.dumps(body.get("response_format") or {}):
        return openai_reply(path, body)
    reply = completion(body, json.dumps(CARD))
    sent = count(json.dumps(body["messages"])) + count(
        json.dumps(body.get("response_format"))
    )
    reply["usage"] = {
        "prompt_tokens": sent,
        "completion_tokens": spend.CARD_OUTPUT_TOKENS,
        "total_tokens": sent + spend.CARD_OUTPUT_TOKENS,
    }
    return reply


def local_card(path: str, body: dict) -> dict:
    if path == "/api/tags":
        return {"models": [{"name": LOCAL_TAG}]}
    return {
        "model": body.get("model", "stub"),
        "message": {"role": "assistant", "content": json.dumps(CARD)},
        "done": True,
        "done_reason": "stop",
    }


def chats(stub) -> list:
    """Requests that asked a model for something, not for its tags or model list."""
    return [r for r in stub.requests if not r.path.endswith(("/api/tags", "/models"))]


def card_of(name: str) -> dict:
    with database.read() as connection:
        document_id = connection.execute(
            sa.select(database.documents.c.id).where(
                database.documents.c.file_name == name
            )
        ).scalar_one()
    return cards.get(document_id)


@pytest.fixture
def openai(monkeypatch):
    with stub_openai() as stub:
        monkeypatch.setenv(STUB_OPENAI_ENV, stub.url)
        stub.reply = billed
        yield stub


@pytest.fixture
def openrouter(monkeypatch):
    with stub_openai() as stub:
        monkeypatch.setenv(STUB_OPENROUTER_ENV, stub.url)
        stub.reply = billed
        yield stub


@pytest.fixture
def local(monkeypatch):
    with stub_ollama() as stub:
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        stub.reply = local_card
        yield stub


@pytest.fixture
def listed(library):
    model_catalog.store_openrouter(
        [row for row in map(model_catalog.to_row, OPENROUTER_LIST) if row]
    )


@pytest.fixture
def key():
    """The key for whichever provider a test names, removed afterwards."""
    saved = []

    def save(provider: str) -> None:
        keychain.set_key(f"test-{provider}-key", provider)
        saved.append(provider)

    yield save
    for provider in saved:
        keychain.delete_key(provider)


def folder_of(
    tmp_path, documents: int, sensitivity: str = states.STANDARD, on: bool = True
) -> dict:
    root = tmp_path / "w"
    root.mkdir()
    for n in range(documents):
        (root / f"Doc {n}.txt").write_text(f"Document {n}. The rent is due monthly.\n")
    return sources.add_source(str(root), sensitivity, auto_organize=on)


class TestOpenRouterCards:
    def test_a_backlog_goes_one_card_at_a_time_at_full_price(
        self, listed, openrouter, key, tmp_path
    ):
        key(providers.OPENROUTER)
        settings.put(settings.MODEL_HELPER, GPT_OSS)
        # Quoted before it's turned on, as the app does: written cards aren't in an
        # estimate, which prices only the cards still to write.
        source = folder_of(tmp_path, spend.BATCH_ABOVE + 1, on=False)
        drain()
        estimate = sources.organize_estimate(source["id"])
        assert estimate["batched"] is False
        assert estimate["provider"] == "OpenRouter"
        sources.set_switches(source["id"], auto_organize=True)
        for document_id in sources.waiting_for_cards():
            organize.run({"document_id": document_id})
        assert [r for r in openrouter.requests if "/batches" in r.path] == []
        assert len(chats(openrouter)) == spend.BATCH_ABOVE + 1
        assert card_of("Doc 0.txt")["written_by"] == "OpenAI: gpt-oss-120b"
        with database.read() as connection:
            bill = connection.execute(
                sa.select(sa.func.sum(database.egress_log.c.cost_usd))
            ).scalar_one()
        assert estimate["backfill_usd"] == pytest.approx(bill, rel=0.2)


class TestNoReadyKey:
    def test_the_local_model_writes_the_card_and_the_switch_says_why(
        self, library, openai, local, tmp_path
    ):
        settings.put(settings.MODEL_HELPER, LUNA)
        source = folder_of(tmp_path, 1)
        drain()
        assert chats(openai) == []
        assert card_of("Doc 0.txt")["written_by"] == LOCAL_TAG
        unavailable = sources.organize_estimate(source["id"])["unavailable"]
        assert "needs an OpenAI key" in str(unavailable)

    def test_with_no_local_model_either_the_card_waits_with_the_reason(
        self, library, openai, local, tmp_path
    ):
        settings.put(settings.MODEL_HELPER, LUNA)
        local.reply = lambda path, body: (
            {"models": []} if path == "/api/tags" else local_card(path, body)
        )
        folder_of(tmp_path, 1)
        drain()
        card = card_of("Doc 0.txt")
        assert card["state"] == states.PENDING
        assert "needs an OpenAI key" in card["state_reason"]
        assert f"{LOCAL_TAG}, isn't ready" in card["state_reason"]
        assert chats(openai) == []
        assert chats(local) == []

    def test_it_cant_be_turned_on_without_the_helpers_key(
        self, library, key, tmp_path, app_client
    ):
        settings.put(settings.MODEL_HELPER, LUNA)
        root = tmp_path / "w"
        root.mkdir()
        body = {"path": str(root), "sensitivity": "standard", "auto_organize": True}
        refused = app_client.post("/api/sources", json=body)
        assert refused.status_code == 409
        assert "needs an OpenAI key" in refused.json()["detail"]
        added = app_client.post("/api/sources", json={**body, "auto_organize": False})
        url = f"/api/sources/{added.json()['id']}"
        refused = app_client.patch(url, json={"auto_organize": True})
        assert refused.status_code == 409
        estimate = app_client.get(f"{url}/organize-estimate").json()
        assert (estimate["key_ready"], estimate["model_name"]) == (False, "GPT-6 Luna")
        assert (estimate["input_usd"], estimate["output_usd"]) == (0.1, 0.5)
        key(providers.OPENAI)
        shown = {"model": LUNA, "input_usd": 0.1, "output_usd": 0.5, "batched": False}
        on = app_client.patch(url, json={"auto_organize": True, **shown})
        assert on.status_code == 200
        keychain.delete_key(providers.OPENAI)
        assert app_client.patch(url, json={"auto_organize": False}).status_code == 200

    def test_it_goes_on_only_at_the_helper_and_price_shown(
        self, library, key, tmp_path, app_client
    ):
        # The estimate was for Haiku, and the helper was changed to a model at 100
        # times its price before Turn on.
        key(providers.ANTHROPIC)
        key(providers.OPENAI)
        settings.put(settings.MODEL_HELPER, "anthropic:claude-haiku-5-5")
        source = folder_of(tmp_path, 1)
        sources.set_switches(source["id"], auto_organize=False)
        url = f"/api/sources/{source['id']}"
        estimate = app_client.get(f"{url}/organize-estimate").json()
        shown = {
            "model": estimate["model"],
            "input_usd": estimate["input_usd"],
            "output_usd": estimate["output_usd"],
            "batched": estimate["batched"],
        }
        assert shown == {
            "model": "anthropic:claude-haiku-5-5",
            "input_usd": 0.1,
            "output_usd": 0.5,
            "batched": False,
        }
        settings.put(settings.MODEL_HELPER, "openai:gpt-6-astra")
        for sent in (shown, {}):
            refused = app_client.patch(url, json={"auto_organize": True, **sent})
            assert refused.status_code == 409
            assert refused.json()["detail"] == sources.ORGANIZE_COST_CHANGED
        assert sources.get(source["id"])["auto_organize"] is False
        settings.put(settings.MODEL_HELPER, "anthropic:claude-haiku-5-5")
        on = app_client.patch(url, json={"auto_organize": True, **shown})
        assert on.status_code == 200
        assert sources.get(source["id"])["auto_organize"] is True

    def test_it_goes_on_only_while_the_batch_price_shown_applies(
        self, library, key, tmp_path, app_client, monkeypatch
    ):
        # Shown at the batch price, then the backlog shrank, so the
        # folder's cards would go at full price, approved at half.
        key(providers.ANTHROPIC)
        settings.put(settings.MODEL_HELPER, "anthropic:claude-haiku-5-5")
        source = folder_of(tmp_path, 1)
        sources.set_switches(source["id"], auto_organize=False)
        url = f"/api/sources/{source['id']}"
        monkeypatch.setattr(spend, "BATCH_ABOVE", -1)
        estimate = app_client.get(f"{url}/organize-estimate").json()
        assert estimate["batched"] is True
        shown = {
            key: estimate[key]
            for key in ("model", "input_usd", "output_usd", "batched")
        }
        monkeypatch.setattr(spend, "BATCH_ABOVE", 50)  # the rest of the backlog done
        refused = app_client.patch(url, json={"auto_organize": True, **shown})
        assert refused.status_code == 409
        assert sources.get(source["id"])["auto_organize"] is False
        again = app_client.get(f"{url}/organize-estimate").json()
        shown["batched"] = again["batched"]
        on = app_client.patch(url, json={"auto_organize": True, **shown})
        assert on.status_code == 200

    def test_its_amounts_and_rates_come_from_one_price(
        self, library, key, tmp_path, monkeypatch
    ):
        # A refresh between two reads of the price could pair old amounts with new
        # rates, approved as shown.
        key(providers.OPENAI)
        settings.put(settings.MODEL_HELPER, LUNA)
        source = folder_of(tmp_path, 1)
        prices = itertools.chain([(0.1, 0.5)], itertools.repeat((1.0, 5.0)))
        monkeypatch.setattr(model_catalog, "price", lambda _model: next(prices))
        found = sources.organize_estimate(source["id"])
        assert (found["input_usd"], found["output_usd"]) == (0.1, 0.5)
        per = (
            spend.EXCERPT_TOKENS + spend.CARD_INSTRUCTION_TOKENS
        ) * 0.1 + spend.CARD_OUTPUT_TOKENS * 0.5
        assert found["per_document_usd"] == round(per / 1_000_000, 4)
        assert "price" not in found

    def test_a_batched_backfill_shows_the_batch_rates(
        self, library, key, tmp_path, monkeypatch
    ):
        # The rates shown are the ones the backfill is charged at.
        key(providers.OPENAI)
        settings.put(settings.MODEL_HELPER, LUNA)
        source = folder_of(tmp_path, 1)
        found = sources.organize_estimate(source["id"])
        assert found["batched"] is False
        assert (found["backfill_input_usd"], found["backfill_output_usd"]) == (0.1, 0.5)
        real = spend.card_estimate
        monkeypatch.setattr(
            spend,
            "card_estimate",
            lambda *a, **k: {**real(*a, **k), "batched": True},
        )
        found = sources.organize_estimate(source["id"])
        assert (found["backfill_input_usd"], found["backfill_output_usd"]) == (
            pytest.approx(0.05),
            pytest.approx(0.25),
        )
        assert (found["input_usd"], found["output_usd"]) == (0.1, 0.5), "each new one"

    def test_turning_it_on_is_refused_for_a_private_folder(self, library, tmp_path):
        root = tmp_path / "p"
        root.mkdir()
        source = sources.add_source(str(root), states.PRIVATE)
        with pytest.raises(refusals.Refused):
            sources.set_switches(source["id"], auto_organize=True)


class TestNoKnownPrice:
    def test_the_card_waits_and_neither_model_is_asked(
        self, listed, openrouter, local, key, tmp_path
    ):
        key(providers.OPENROUTER)
        settings.put(settings.MODEL_HELPER, UNPRICED)
        folder_of(tmp_path, 1)
        drain()
        card = card_of("Doc 0.txt")
        assert card["state"] == states.PENDING
        assert "No known price" in card["state_reason"]
        assert chats(openrouter) == []
        assert chats(local) == []

    @pytest.fixture
    def capped_on_openrouter(self, listed, openrouter, key, monkeypatch):
        """At the cap, with an OpenRouter key: its stub."""
        key(providers.OPENROUTER)
        monkeypatch.setattr(spend, "over_cap", lambda: True)
        return openrouter

    def test_at_the_cap_the_card_still_waits_and_neither_model_is_asked(
        self, capped_on_openrouter, local, tmp_path
    ):
        # The cap's local fallback mustn't skip the price rule.
        openrouter = capped_on_openrouter
        settings.put(settings.MODEL_HELPER, UNPRICED)
        folder_of(tmp_path, 1)
        drain()
        card = card_of("Doc 0.txt")
        assert card["state"] == states.PENDING
        assert "No known price" in card["state_reason"]
        assert chats(openrouter) == []
        assert chats(local) == []

    def test_at_the_cap_a_priced_helper_falls_back_to_the_local_model(
        self, capped_on_openrouter, local, tmp_path
    ):
        openrouter = capped_on_openrouter
        settings.put(settings.MODEL_HELPER, GPT_OSS)
        folder_of(tmp_path, 1)
        drain()
        assert card_of("Doc 0.txt")["written_by"] == LOCAL_TAG
        assert chats(openrouter) == []
