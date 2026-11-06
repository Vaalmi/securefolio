"""OpenAI's Batch API: a backlog for an OpenAI helper goes as one batch at half price,
every row of the batch table holds with OpenAI's states, and a batch keeps its own
provider, model and key."""

import time

import pytest
import sqlalchemy as sa

from backend import (
    cards,
    database,
    documents,
    keychain,
    providers,
    settings,
    sources,
    spend,
    states,
)
from testing.openai_batches import (
    LUNA,
    LUNA_PRICE,
    anthropic_stub,
    backlog,
    billed,
    items,
    openai_batches,
    submitted,
    the_batch,
    written_by,
)
from worker import batches, organize


@pytest.fixture
def openai(library, monkeypatch):
    with openai_batches(monkeypatch) as stub:
        yield stub


@pytest.fixture
def anthropic(monkeypatch):
    with anthropic_stub(monkeypatch) as stub:
        yield stub


class TestTheBatch:
    def test_a_backlog_goes_to_openai_at_half_price(self, openai, tmp_path):
        source_id = backlog(tmp_path)
        assert sources.organize_estimate(source_id)["batched"] is True
        batches.tick()
        batch = the_batch()
        assert (batch["provider"], batch["model"], batch["state"]) == (
            providers.OPENAI,
            LUNA,
            batches.SUBMITTED,
        )
        (sent,) = openai.side.batches.values()
        assert len(sent["lines"]) == spend.BATCH_ABOVE + 1
        assert sent["metadata"] == {"submission_id": batch["submission_id"]}
        line = sent["lines"][0]
        assert line["body"]["model"] == "gpt-6-luna"
        assert line["body"]["response_format"]["json_schema"]["name"] == "card"
        openai.side.finish(batch["provider_id"])
        batches.tick()
        assert the_batch()["state"] == batches.APPLIED
        assert written_by() == {"GPT-6 Luna"}
        (row,) = billed()
        whole = 51 * (1000 * LUNA_PRICE[0] + 100 * LUNA_PRICE[1]) / 1_000_000
        assert row["outcome"] == "sent"
        assert row["cost_usd"] == pytest.approx(whole / 2)

    def test_a_restart_before_sending_is_unknown_and_never_resent(
        self, openai, tmp_path, monkeypatch
    ):
        backlog(tmp_path)
        real = batches._submit_planned  # noqa: SLF001
        monkeypatch.setattr(batches, "_submit_planned", lambda: None)
        batches.tick()  # planned only
        batch = the_batch()
        batches._move(  # noqa: SLF001
            batch["id"], batches.PLANNED, batches.SUBMITTING, marked_at=time.time()
        )
        monkeypatch.setattr(batches, "_submit_planned", real)
        batches.tick()  # the restart finds it marked, with no id
        assert the_batch()["state"] == batches.SUBMIT_UNKNOWN
        assert openai.side.batches == {}

    def test_one_accepted_before_its_id_was_saved_is_found_by_its_metadata(
        self, openai, tmp_path, monkeypatch
    ):
        batch = submitted(openai, tmp_path)
        lost = batch["provider_id"]
        batches._move(  # noqa: SLF001
            batch["id"], batches.SUBMITTED, batches.SUBMIT_UNKNOWN, provider_id=None
        )
        batches.tick()
        found = the_batch()
        assert (found["state"], found["provider_id"]) == (batches.SUBMITTED, lost)
        assert len(openai.side.batches) == 1, "never sent again"
        openai.side.finish(lost)
        batches.tick()
        assert the_batch()["state"] == batches.APPLIED
        assert len(billed()) == 1


class TestEndings:
    @pytest.mark.parametrize("status", ["expired", "cancelled"])
    def test_an_ended_batch_settles_what_came_back_and_queues_the_rest(
        self, openai, tmp_path, status
    ):
        batch = submitted(openai, tmp_path)
        sent = openai.side.batches[batch["provider_id"]]["lines"]
        answered = {line["custom_id"] for line in sent[:10]}
        openai.side.finish(batch["provider_id"], status, answered)
        batches.tick()
        states_now = items()
        assert sum(s == batches.ITEM_APPLIED for s in states_now.values()) == 10
        assert sum(s == batches.ITEM_DROPPED for s in states_now.values()) == 41
        (row,) = billed()
        whole = 10 * (1000 * LUNA_PRICE[0] + 100 * LUNA_PRICE[1]) / 1_000_000
        assert row["cost_usd"] == pytest.approx(whole / 2)

    def test_a_failed_batch_sends_every_card_back(self, openai, tmp_path):
        batch = submitted(openai, tmp_path)
        openai.side.finish(batch["provider_id"], "failed")
        batches.tick()
        assert set(items().values()) == {batches.ITEM_DROPPED}
        assert written_by() == {cards.BASIC}

    @pytest.mark.parametrize("change", ["revision", "private", "organizing off"])
    def test_a_result_no_longer_wanted_is_settled_not_applied_and_queued_again(
        self, openai, tmp_path, change
    ):
        source_id = backlog(tmp_path)
        batches.tick()
        batch = the_batch()
        first = next(iter(items()))
        if change == "revision":
            with database.write() as connection:
                connection.execute(
                    database.documents.update()
                    .where(database.documents.c.id == first)
                    .values(active_revision_id="another")
                )
        elif change == "private":
            with database.write() as connection:
                connection.execute(
                    database.documents.update()
                    .where(database.documents.c.id == first)
                    .values(sensitivity=states.PRIVATE)
                )
        else:
            sources.set_switches(source_id, auto_organize=False)
        openai.side.finish(batch["provider_id"])
        batches.tick()
        assert items()[first] == batches.ITEM_DROPPED
        assert cards.get(first)["written_by"] == cards.BASIC
        (row,) = billed()
        whole = 51 * (1000 * LUNA_PRICE[0] + 100 * LUNA_PRICE[1]) / 1_000_000
        assert row["cost_usd"] == pytest.approx(whole / 2), "paid for, so settled"
        with database.read() as connection:
            queued = connection.execute(
                sa.select(database.jobs.c.id).where(
                    database.jobs.c.idempotency_key == f"organize:{first}"
                )
            ).all()
        assert queued

    def test_a_restart_while_results_are_applied_applies_and_bills_once(
        self, openai, tmp_path, monkeypatch
    ):
        batch = submitted(openai, tmp_path)
        openai.side.finish(batch["provider_id"])
        real = organize.apply
        calls = []

        def crash_on_the_fifth(*given, **named):
            calls.append(given[0])
            if len(calls) == 5:
                raise RuntimeError("the worker stops here")
            return real(*given, **named)

        monkeypatch.setattr(organize, "apply", crash_on_the_fifth)
        with pytest.raises(RuntimeError):
            batches.tick()
        monkeypatch.setattr(organize, "apply", real)
        batches.tick()
        assert the_batch()["state"] == batches.APPLIED
        assert set(items().values()) == {batches.ITEM_APPLIED}
        (row,) = billed()
        whole = 51 * (1000 * LUNA_PRICE[0] + 100 * LUNA_PRICE[1]) / 1_000_000
        assert row["cost_usd"] == pytest.approx(whole / 2)


class TestItsOwnProvider:
    def test_a_helper_changed_while_it_runs_doesnt_move_it(
        self, openai, anthropic, tmp_path
    ):
        batch = submitted(openai, tmp_path)
        settings.put(settings.MODEL_HELPER, "anthropic:claude-haiku-5-5")
        openai.side.finish(batch["provider_id"])
        batches.tick()
        assert the_batch()["state"] == batches.APPLIED
        assert [r for r in anthropic.requests if "/batches" in r.path] == []
        assert written_by() == {"GPT-6 Luna"}

    def test_it_waits_for_its_own_providers_key(self, openai, anthropic, tmp_path):
        batch = submitted(openai, tmp_path)
        keychain.delete_key(providers.OPENAI)
        seen = len(openai.requests)
        openai.side.finish(batch["provider_id"])
        batches.tick()
        assert len(openai.requests) == seen, "no call without its key"
        assert the_batch()["state"] == batches.SUBMITTED
        assert anthropic.requests == []
        keychain.set_key("test-openai-key", providers.OPENAI)
        batches.tick()
        assert the_batch()["state"] == batches.APPLIED

    def test_a_document_gone_private_while_planned_is_never_sent(
        self, openai, tmp_path, monkeypatch
    ):
        backlog(tmp_path)
        real = batches._submit_planned  # noqa: SLF001
        monkeypatch.setattr(batches, "_submit_planned", lambda: None)
        batches.tick()
        monkeypatch.setattr(batches, "_submit_planned", real)
        first = next(iter(items()))
        with database.write() as connection:
            connection.execute(
                database.documents.update()
                .where(database.documents.c.id == first)
                .values(sensitivity=states.PRIVATE)
            )
        batches.tick()
        (sent,) = openai.side.batches.values()
        assert all(not line["custom_id"].endswith(first) for line in sent["lines"])
        assert len(sent["lines"]) == spend.BATCH_ABOVE
        assert documents.get(first)["sensitivity"] == states.PRIVATE
