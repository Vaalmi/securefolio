"""The catalog: what each model can do and costs, and OpenRouter's list through its
state machine. The fixtures are each provider's list, with OpenRouter's recorded."""

from contextlib import contextmanager

import pytest
import sqlalchemy as sa

from agent import api, models
from backend import database, keychain, model_catalog, providers, settings, spend
from backend.build_mode import STUB_OPENROUTER_ENV
from testing.stub_models import OPENROUTER_LIST, stub_openai

DAY = 24 * 3600
GPT_OSS = "openrouter:openai/gpt-oss-120b"
OR_CLAUDE = "openrouter:anthropic/claude-sonnet-5-5"
AUTO = "openrouter:openrouter/auto"


def store(entries=OPENROUTER_LIST) -> None:
    model_catalog.store_openrouter(
        [row for row in map(model_catalog.to_row, entries) if row]
    )


def stored_ids() -> set[str]:
    with database.read() as connection:
        return set(
            connection.execute(sa.select(database.openrouter_models.c.id)).scalars()
        )


@pytest.fixture
def openrouter(library, monkeypatch):
    keychain.set_key("test-openrouter-key", providers.OPENROUTER)
    with stub_openai() as stub:
        monkeypatch.setenv(STUB_OPENROUTER_ENV, stub.url)
        yield stub
    keychain.delete_key(providers.OPENROUTER)


class TestTheCheckedList:
    def test_every_checked_model_has_a_price_and_its_date(self):
        assert model_catalog.PRICES_AS_OF == "2026-11-05"
        for model in model_catalog.CHECKED:
            assert model_catalog.price(model.id) is not None, model.id
            assert model.provider in (providers.ANTHROPIC, providers.OPENAI)

    def test_claudes_three_and_openais_short_list(self):
        ids = {model.id for model in model_catalog.CHECKED}
        assert {
            "anthropic:claude-sonnet-5-5",
            "anthropic:claude-opus-5-5",
            "anthropic:claude-haiku-5-5",
            "openai:gpt-6-astra",
            "openai:gpt-6.1-sol",
            "openai:gpt-6.1-terra",
            "openai:gpt-6-luna",
        } == ids

    def test_an_unknown_model_has_no_price(self, library):
        assert model_catalog.price("openai:gpt-unknown") is None
        assert model_catalog.price("not-a-model-id") is None
        assert model_catalog.get("anthropic:claude-retired") is None


class TestWhatAModelCanDo:
    def test_to_row_reads_capabilities_and_prices_per_million(self):
        row = model_catalog.to_row(OPENROUTER_LIST[0])
        assert row is not None
        assert row["input_usd"] == pytest.approx(0.1)
        assert row["output_usd"] == pytest.approx(0.5)
        assert (row["images"], row["structured"]) == (False, True)

    def test_a_price_set_per_request_is_no_known_price(self):
        row = model_catalog.to_row(OPENROUTER_LIST[2])
        assert row is not None
        assert (row["input_usd"], row["output_usd"]) == (None, None)

    def test_a_row_with_no_id_is_dropped(self):
        assert model_catalog.to_row({"name": "nameless"}) is None

    def test_a_missing_price_is_no_known_price(self):
        row = model_catalog.to_row(
            {
                "id": "x/y",
                "pricing": {"prompt": "nope"},
                "architecture": {"output_modalities": ["text"]},
            }
        )
        assert row is not None
        assert row["input_usd"] is None

    def test_a_model_that_returns_no_text_is_dropped(self, library):
        # Every job SecureFolio gives a model needs text back.
        drawing = {
            "id": "someone/draws",
            "name": "Draws pictures",
            "pricing": {"prompt": "0.000001", "completion": "0.000001"},
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["image"],
            },
            "supported_parameters": ["structured_outputs"],
        }
        assert model_catalog.to_row(drawing) is None
        assert model_catalog.to_row({**drawing, "architecture": {}}) is None
        store([*OPENROUTER_LIST, drawing])
        answer = {model.id for model in model_catalog.for_task("answer")}
        assert "openrouter:someone/draws" not in answer
        assert GPT_OSS in answer

    def test_a_model_is_offered_only_for_what_it_can_do(self, library):
        store()
        page = {model.id for model in model_catalog.for_task("page")}
        helper = {model.id for model in model_catalog.for_task("helper")}
        answer = {model.id for model in model_catalog.for_task("answer")}
        assert GPT_OSS not in page  # no image input
        assert OR_CLAUDE in page
        assert GPT_OSS in helper
        assert OR_CLAUDE in helper
        assert AUTO not in answer  # no known price
        assert {GPT_OSS, OR_CLAUDE} <= answer

    def test_a_model_with_no_structured_output_isnt_a_helper(self, library):
        store([{**OPENROUTER_LIST[0], "supported_parameters": []}])
        assert GPT_OSS not in {model.id for model in model_catalog.for_task("helper")}

    def test_an_openrouter_row_is_priced_from_its_snapshot(self, library):
        store()
        assert spend.cost(GPT_OSS, 1_000_000, 1_000_000) == pytest.approx(0.6)


class TestTheOpenRouterList:
    def test_none_until_fetched(self, library):
        assert model_catalog.openrouter_state()["state"] == "none"

    def test_fetched_through_the_gateway_and_stored_with_its_date(self, openrouter):
        models.fetch_openrouter_models()
        assert stored_ids() == {r["id"] for r in OPENROUTER_LIST}
        state = model_catalog.openrouter_state()
        assert state["state"] == "fresh"
        assert state["failed"] is None
        assert openrouter.requests[-1].path.endswith("/models")

    def test_stale_at_seven_days_and_still_used(self, library):
        store()
        fetched = float(settings.get(settings.OPENROUTER_FETCHED))
        assert model_catalog.openrouter_state(fetched + 7 * DAY - 1)["state"] == "fresh"
        assert model_catalog.openrouter_state(fetched + 7 * DAY)["state"] == "stale"
        assert model_catalog.price(GPT_OSS) is not None

    def test_never_fetched_in_offline_mode(self, openrouter):
        settings.put(settings.OFFLINE, True)
        with pytest.raises(models.NoConnection):
            models.fetch_openrouter_models()
        assert openrouter.requests == []
        assert model_catalog.openrouter_state()["state"] == "none"
        assert "Offline" in model_catalog.openrouter_state()["failed"]

    def test_a_failed_refresh_keeps_the_list_as_it_was(self, openrouter):
        store()
        before = stored_ids()
        fetched = settings.get(settings.OPENROUTER_FETCHED)
        openrouter.status = 500
        with pytest.raises(models.NoConnection):
            models.fetch_openrouter_models()
        assert stored_ids() == before
        state = model_catalog.openrouter_state()
        assert state["fetched_at"] == fetched
        assert "couldn't be fetched" in state["failed"]

    def test_a_chosen_model_left_out_is_kept_at_its_last_price(self, library):
        store()
        settings.put(settings.MODEL_HELPER, GPT_OSS)
        store([OPENROUTER_LIST[1]])
        kept = model_catalog.get(GPT_OSS)
        assert kept is not None
        assert kept.listed is False
        assert model_catalog.price(GPT_OSS) == pytest.approx((0.1, 0.5))
        assert GPT_OSS not in {m.id for m in model_catalog.for_task("helper")}
        assert model_catalog.get(AUTO) is None  # not chosen: dropped

    def test_a_late_fetch_doesnt_undo_a_newer_one(self, openrouter, monkeypatch):
        # An older fetch's answer, arriving after a newer one's,
        # replaced the newer list.
        real = models._openrouter_get  # noqa: SLF001
        newer = [OPENROUTER_LIST[1]]
        calls: list[int] = []

        def slow_first(path, key):
            calls.append(1)
            if len(calls) == 1:
                openrouter.reply = lambda _p, _b: {"data": newer}
                models.fetch_openrouter_models()  # a newer fetch, done first
                openrouter.reply = None  # the older one answers with the full list
            return real(path, key)

        monkeypatch.setattr(models, "_openrouter_get", slow_first)
        models.fetch_openrouter_models()
        assert stored_ids() == {newer[0]["id"]}

    def test_a_model_chosen_as_the_list_is_stored_is_kept(self, library, monkeypatch):
        # The chosen models were read before the write, so one
        # chosen in between was dropped. They're read under the write lock now.
        store()
        real_write = database.write
        held = {"lock": False}
        seen: list[bool] = []

        @contextmanager
        def write():
            with real_write() as connection:
                held["lock"] = True
                try:
                    yield connection
                finally:
                    held["lock"] = False

        real_chosen = model_catalog.chosen

        def chosen():
            seen.append(held["lock"])
            return real_chosen()

        monkeypatch.setattr(database, "write", write)
        monkeypatch.setattr(model_catalog, "chosen", chosen)
        store([OPENROUTER_LIST[1]])
        assert seen == [True]

    def test_a_kept_model_listed_again_is_listed(self, library):
        store()
        settings.put(settings.MODEL_HELPER, GPT_OSS)
        store([OPENROUTER_LIST[1]])
        store()
        kept = model_catalog.get(GPT_OSS)
        assert kept is not None
        assert kept.listed is True

    def test_choosing_an_unlisted_model_is_refused(self, openrouter, app_client):
        store()
        made = app_client.patch(
            "/api/settings", json={"model_answer": "openrouter:someone/not-listed"}
        )
        assert made.status_code == 422
        assert settings.get(settings.MODEL_ANSWER) != "openrouter:someone/not-listed"

    def test_choosing_a_listed_model_is_kept(self, openrouter, app_client):
        store()
        made = app_client.patch("/api/settings", json={"model_answer": GPT_OSS})
        assert made.status_code == 200
        assert settings.get(settings.MODEL_ANSWER) == GPT_OSS

    def test_a_model_for_a_task_it_cant_do_is_refused(self, openrouter, app_client):
        store()
        made = app_client.patch("/api/settings", json={"model_page": GPT_OSS})
        assert made.status_code == 422


class TestAPriceAcceptedIsKept:
    """A refresh while a call is out can't change or lose its price."""

    @pytest.fixture
    def gone_after_sending(self, library):
        store()
        settings.put(settings.MODEL_ANSWER, GPT_OSS)
        price = api.price_of(GPT_OSS)
        assert price is not None  # read once, at the call's gate
        entry = api.log_attempt(GPT_OSS, "answer", [], api.Quote(price, 0.01))
        batch = api.log_batch_attempt(GPT_OSS, [], api.Quote(price, 0.01), "sub-1")
        # The owner picks another model, and a refresh no longer lists this one.
        settings.put(settings.MODEL_ANSWER, "anthropic:claude-sonnet-5-5")
        store([row for row in OPENROUTER_LIST if row["id"] != "openai/gpt-oss-120b"])
        assert model_catalog.get(GPT_OSS) is None
        return api, entry, batch

    def test_an_answer_settles_at_the_price_it_went_at(self, gone_after_sending):
        api, entry, _ = gone_after_sending
        spent = api.log_sent(entry, GPT_OSS, {"input_tokens": 1000, "output_tokens": 0})
        assert spent == pytest.approx(1000 * 0.1 / 1_000_000)

    def test_a_batch_settles_at_half_the_price_it_went_at(self, gone_after_sending):
        api, _, batch = gone_after_sending
        assert batch is not None
        api.settle_batch("sub-1", {"input_tokens": 1000, "output_tokens": 0})
        with database.read() as connection:
            cost = connection.execute(
                sa.select(database.egress_log.c.cost_usd).where(
                    database.egress_log.c.id == batch
                )
            ).scalar()
        assert cost == pytest.approx(1000 * 0.1 / 1_000_000 / 2)
