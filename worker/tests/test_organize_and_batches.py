"""Cards from a model, and a backlog through the Batches API."""

import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from agent import models
from backend import (
    cards,
    database,
    documents,
    keychain,
    settings,
    sources,
    spend,
    states,
)
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OLLAMA_ENV
from backend.tokens import count
from testing.queue import drain
from testing.stub_models import stub_anthropic, stub_ollama
from worker import batches, organize

DATA = Path(__file__).parents[2] / "data" / "library"
CARD = {
    "title": "Lease",
    "summary": "A lease for unit 3B.",
    "doc_type": "Contract",
    "dates": [],
    "people": [],
    "organizations": [],
    "amounts": ["$2,350.00"],
    "tags": ["home"],
}

HAIKU = (0.10, 0.50)  # Claude Haiku 5.5, per million tokens, as the catalog has it


def local_card(path: str, body: dict) -> dict:
    if path == "/api/tags":
        return {"models": [{"name": "gemma4:26b"}]}
    return {
        "model": body.get("model", "stub"),
        "message": {"role": "assistant", "content": json.dumps(CARD)},
        "done": True,
        "done_reason": "stop",
    }


def only_document() -> dict:
    with database.read() as connection:
        row = connection.execute(sa.select(database.documents)).mappings().one()
    return dict(row)


@pytest.fixture
def folder(library, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "Lease.txt").write_text("A lease.\n")
    return root


class TestLocal:
    def test_a_basic_card_waits_with_no_model(self, folder):
        sources.add_source(str(folder), states.STANDARD)
        drain()
        card = cards.get(only_document()["id"])
        assert card["state"] == states.PENDING
        assert card["written_by"] == cards.BASIC

    def test_the_local_model_writes_it(self, folder, monkeypatch):
        with stub_ollama() as local:
            monkeypatch.setenv(STUB_OLLAMA_ENV, local.url)
            local.reply = local_card
            sources.add_source(str(folder), states.STANDARD)
            drain()
        card = cards.get(only_document()["id"])
        assert card["state"] == states.COMPLETE
        assert card["doc_type"] == "Contract"
        assert (
            card["written_by"] == "gemma4:26b"
        )  # the local model for cards, by its tag

    def test_an_edit_survives_a_rewrite_and_a_reindex(self, folder, monkeypatch):
        with stub_ollama() as local:
            monkeypatch.setenv(STUB_OLLAMA_ENV, local.url)
            local.reply = local_card
            sources.add_source(str(folder), states.STANDARD)
            drain()
            document = only_document()
            cards.edit_card(document["id"], {"summary": "My own words."})
            documents.reindex(document["id"])
            drain()
        card = cards.get(document["id"])
        assert card["summary"] == "My own words."
        assert card["doc_type"] == "Contract"

    def test_pending_cards_are_queued_once_the_model_is_there(
        self, folder, monkeypatch
    ):
        sources.add_source(str(folder), states.STANDARD)
        drain()
        assert organize.queue_pending_when_ready() == 0
        with stub_ollama() as local:
            monkeypatch.setenv(STUB_OLLAMA_ENV, local.url)
            local.reply = local_card
            assert organize.queue_pending_when_ready() == 1
            drain()
        assert cards.get(only_document()["id"])["state"] == states.COMPLETE


@pytest.fixture
def cloud(monkeypatch):
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        yield stub
    keychain.delete_key()


def haiku_card(_path: str, body: dict) -> dict:
    return {
        "id": "msg",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": [{"type": "text", "text": json.dumps(CARD)}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


class TestHaiku:
    def test_an_opted_in_folder_uses_it(self, folder, cloud):
        cloud.reply = haiku_card
        sources.add_source(str(folder), states.STANDARD, auto_organize=True)
        drain()
        card = cards.get(only_document()["id"])
        assert card["written_by"] == "Claude Haiku 5.5"
        # the request names the provider's own model; SecureFolio's id adds its provider
        assert cloud.requests[0].body["model"] == "claude-haiku-5-5"

    def test_a_private_folder_is_never_sent(self, folder, cloud):
        sources.add_source(str(folder), states.PRIVATE, auto_organize=True)
        drain()
        assert cloud.requests == []

    def test_a_card_whose_consent_went_while_it_was_written_isnt_kept(
        self, folder, cloud
    ):
        # Organizing turned off while the model wrote the card.
        source = sources.add_source(str(folder), states.STANDARD, auto_organize=True)

        def reply(path, body):
            sources.set_switches(source["id"], auto_organize=False)
            return haiku_card(path, body)

        cloud.reply = reply
        drain()
        assert cloud.requests, "it went while consent stood"
        card = cards.get(only_document()["id"])
        assert card["written_by"] != "Claude Haiku 5.5"

    def test_a_folder_that_didnt_opt_in_is_never_sent(self, folder, cloud):
        sources.add_source(str(folder), states.STANDARD)
        drain()
        assert cloud.requests == []

    @pytest.mark.parametrize(
        ("spent", "sent"), [(24.99, True), (25.0, False), (30.0, False)]
    )
    def test_background_work_stops_at_the_cap(
        self, folder, cloud, monkeypatch, spent, sent
    ):
        cloud.reply = haiku_card
        monkeypatch.setattr(spend, "spent_this_month", lambda: spent)
        sources.add_source(str(folder), states.STANDARD, auto_organize=True)
        drain()
        assert bool(cloud.requests) is sent

    def test_the_estimate_is_within_a_fifth_of_the_bill(self, library, cloud, tmp_path):
        """The stub bills the real size of each request. This folder is small enough to
        go one by one, at full price; only a backlog gets the Batch price."""

        def billed(_path: str, body: dict) -> dict:
            reply = haiku_card(_path, body)
            sent = count(json.dumps(body["messages"])) + spend.CARD_INSTRUCTION_TOKENS
            reply["usage"] = {
                "input_tokens": sent,
                "output_tokens": spend.CARD_OUTPUT_TOKENS,
            }
            return reply

        cloud.reply = billed
        root = tmp_path / "home"
        shutil.copytree(DATA / "Home", root)
        # Quoted before turning it on, as the app does; no local model writes the
        # cards meanwhile, so each is still pending.
        source = sources.add_source(str(root), states.STANDARD)
        drain()
        estimate = sources.organize_estimate(source["id"])["backfill_usd"]
        sources.set_switches(source["id"], auto_organize=True)
        for document_id in sources.waiting_for_cards():
            organize.run({"document_id": document_id})
        assert sources.waiting_for_cards() == set(), "every card was written"
        with database.read() as connection:
            bill = connection.execute(
                sa.select(sa.func.sum(database.egress_log.c.cost_usd))
            ).scalar_one()
        assert estimate == pytest.approx(bill, rel=0.2)

    def test_only_a_backlog_is_priced_as_a_batch(self, library):
        alone = spend.card_estimate(50 * 1000, 50)
        backlog = spend.card_estimate(51 * 1000, 51)
        assert alone["batched"] is False
        assert backlog["batched"] is True
        assert backlog["backfill_usd"] is not None
        assert alone["backfill_usd"] is not None
        assert backlog["backfill_usd"] < alone["backfill_usd"]


@pytest.mark.parametrize(
    ("count", "groups"),
    [(50, 0), (51, 1), (99, 1), (100, 1), (200, 1), (201, 2), (399, 2), (400, 2)],
)
def test_the_partition(count, groups):
    found = batches.partition([str(n) for n in range(count)])
    assert len(found) == groups
    assert sum(len(g) for g in found) == (count if groups else 0)
    if count >= 100:
        assert all(100 <= len(g) <= 200 for g in found)


class BatchStub:
    """The provider's side of the Batches API, as far as the worker asks."""

    def __init__(self) -> None:
        self.batches: dict[str, dict] = {}
        self.created = 0
        self.fail_next: Exception | None = None

    def reply(self, path: str, body: dict) -> dict:
        if path.endswith("/messages/batches") and body:
            return self._create(body)
        if "?" in path or path.endswith("/messages/batches"):
            return self._list()
        batch_id = path.rsplit("/", maxsplit=1)[-1]
        return self._shape(batch_id)

    def _create(self, body: dict) -> dict:
        self.created += 1
        batch_id = f"msgbatch_{self.created}"
        self.batches[batch_id] = {
            "requests": body["requests"],
            "status": "in_progress",
            "created": datetime.now(UTC),
        }
        return self._shape(batch_id)

    def _shape(self, batch_id: str) -> dict:
        found = self.batches[batch_id]
        return {
            "id": batch_id,
            "type": "message_batch",
            "processing_status": found["status"],
            "request_counts": {
                "processing": 0,
                "succeeded": 0,
                "errored": 0,
                "canceled": 0,
                "expired": 0,
            },
            "created_at": found["created"].isoformat(),
            "expires_at": found["created"].isoformat(),
            "results_url": None,
            "ended_at": None,
            "archived_at": None,
            "cancel_initiated_at": None,
        }

    def _list(self) -> dict:
        data = [self._shape(b) for b in self.batches]
        return {"data": data, "has_more": False, "first_id": None, "last_id": None}


def backlog(tmp_path, count: int) -> None:
    root = tmp_path / "backlog"
    root.mkdir()
    for n in range(count):
        (root / f"Doc {n}.txt").write_text(f"Document {n}.\n")
    sources.add_source(str(root), states.STANDARD, auto_organize=True)
    drain()


class TestBatchStates:
    def test_a_backlog_waits_for_the_batch_path(self, library, cloud, tmp_path):
        cloud.reply = haiku_card
        backlog(tmp_path, batches.ALONE + 1)
        # Each first document in went one by one until the backlog passed the limit.
        assert len(cloud.requests) <= batches.ALONE + 1

    def test_a_crash_after_marking_is_unknown_and_never_resent(
        self, library, cloud, tmp_path, monkeypatch
    ):
        stub = BatchStub()
        cloud.reply = stub.reply
        batch_id = plant_batch(tmp_path, batches.SUBMITTING)
        monkeypatch.setattr(batches.models, "batches_since", lambda _model, _m: [])
        batches.tick()
        assert state_of(batch_id) == batches.SUBMIT_UNKNOWN
        assert stub.created == 0

    def test_an_unknown_found_by_its_submission_is_applied_once(
        self, library, cloud, tmp_path, monkeypatch
    ):
        batch_id = plant_batch(tmp_path, batches.SUBMIT_UNKNOWN, marked=time.time())
        row = row_of(batch_id)
        prefix = f"{row['submission_id']}-"
        items = batches._items_of(batch_id)  # noqa: SLF001
        results = [
            {
                "custom_id": prefix + item["document_id"],
                "fields": CARD,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            for item in items
        ]
        monkeypatch.setattr(
            batches.models,
            "batches_since",
            lambda _model, _m: [{"id": "b1", "status": "ended"}],
        )
        monkeypatch.setattr(batches.models, "batch_results", lambda _model, _i: results)
        batches.tick()
        assert state_of(batch_id) == batches.APPLIED
        assert cards.get(items[0]["document_id"])["state"] == states.COMPLETE

    def test_same_size_and_time_arent_mistaken_for_each_other(
        self, library, cloud, tmp_path, monkeypatch
    ):
        batch_id = plant_batch(tmp_path, batches.SUBMIT_UNKNOWN, marked=time.time())
        others = [
            {"custom_id": "someoneelse-x", "fields": CARD, "usage": {}},
        ]
        monkeypatch.setattr(
            batches.models,
            "batches_since",
            lambda _model, _m: [{"id": "b1", "status": "ended"}],
        )
        monkeypatch.setattr(batches.models, "batch_results", lambda _model, _i: others)
        batches.tick()
        assert state_of(batch_id) == batches.SUBMIT_UNKNOWN

    def test_after_a_day_with_no_match_it_can_go_again(
        self, library, cloud, tmp_path, monkeypatch
    ):
        batch_id = plant_batch(
            tmp_path, batches.SUBMIT_UNKNOWN, marked=time.time() - 26 * 3600
        )
        monkeypatch.setattr(batches.models, "batches_since", lambda _model, _m: [])
        monkeypatch.setattr(batches, "_submit_one", lambda _b: None)
        batches.tick()
        assert state_of(batch_id) == batches.PLANNED

    def test_a_document_gone_private_while_waiting_is_dropped(
        self, library, cloud, tmp_path, monkeypatch
    ):
        batch_id = plant_batch(
            tmp_path, batches.ENDED, provider="b1", marked=time.time()
        )
        row = row_of(batch_id)
        prefix = f"{row['submission_id']}-"
        items = batches._items_of(batch_id)  # noqa: SLF001
        source_id = documents.locations(items[0]["document_id"])[0]["source_id"]
        sources.set_sensitivity(source_id, states.PRIVATE)
        results = [
            {
                "custom_id": prefix + item["document_id"],
                "fields": CARD,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            for item in items
        ]
        monkeypatch.setattr(batches.models, "batch_results", lambda _model, _i: results)
        batches.tick()
        assert state_of(batch_id) == batches.APPLIED
        assert cards.get(items[0]["document_id"])["state"] == states.PENDING

    def test_offline_defers_everything(self, library, cloud, tmp_path, monkeypatch):
        batch_id = plant_batch(tmp_path, batches.SUBMITTED, provider="b1")
        settings.put(settings.OFFLINE, True)
        batches.tick()
        assert state_of(batch_id) == batches.SUBMITTED
        assert cloud.requests == []

    def test_a_submit_that_got_no_answer_is_unknown(
        self, library, cloud, tmp_path, monkeypatch
    ):
        batch_id = plant_batch(tmp_path, batches.PLANNED)

        def lost(*_given):
            raise batches.models.BatchUnknown

        monkeypatch.setattr(batches.models, "submit_batch", lost)
        monkeypatch.setattr(batches, "plan", lambda: 0)
        monkeypatch.setattr(batches.models, "batches_since", lambda _model, _m: [])
        batches.tick()
        assert state_of(batch_id) == batches.SUBMIT_UNKNOWN

    def test_a_refused_submit_can_go_again(self, library, cloud, tmp_path, monkeypatch):
        batch_id = plant_batch(tmp_path, batches.PLANNED)

        def refused(*_given):
            raise batches.models.BatchRefused

        monkeypatch.setattr(batches.models, "submit_batch", refused)
        monkeypatch.setattr(batches, "plan", lambda: 0)
        batches.tick()
        assert state_of(batch_id) == batches.PLANNED


def plant_batch(
    tmp_path, state: str, marked: float | None = None, provider: str | None = None
) -> str:
    root = tmp_path / "planted"
    root.mkdir()
    (root / "One.txt").write_text("One.\n")
    (root / "Two.txt").write_text("Two.\n")
    sources.add_source(str(root), states.STANDARD)
    drain()
    source_id = [s for s in sources.live() if s["name"] == "planted"][0]["id"]
    sources.set_switches(source_id, auto_organize=True)
    with database.read() as connection:
        rows = connection.execute(sa.select(database.documents)).mappings().all()
    helper = str(settings.get(settings.MODEL_HELPER))
    batch_id = batches._plan_one([dict(r) for r in rows], helper)  # noqa: SLF001
    with database.write() as connection:
        connection.execute(
            database.message_batches.update()
            .where(database.message_batches.c.id == batch_id)
            .values(state=state, marked_at=marked or time.time(), provider_id=provider)
        )
    return batch_id


def row_of(batch_id: str) -> dict:
    with database.read() as connection:
        return dict(
            connection.execute(
                sa.select(database.message_batches).where(
                    database.message_batches.c.id == batch_id
                )
            )
            .mappings()
            .one()
        )


def state_of(batch_id: str) -> str:
    return row_of(batch_id)["state"]


class TestRelated:
    def test_a_standard_documents_list_shows_no_private_one(
        self, library, tmp_path, app_client
    ):
        for name, level in (
            ("a", states.STANDARD),
            ("b", states.STANDARD),
            ("c", states.PRIVATE),
        ):
            root = tmp_path / name
            root.mkdir()
            (root / f"{name}.txt").write_text(f"{name}.\n")
            sources.add_source(str(root), level)
        drain()
        with database.read() as connection:
            rows = connection.execute(sa.select(database.documents)).mappings().all()
        for row in rows:
            organize.apply(row["id"], row["active_revision_id"], CARD, "Test")
        by_title = {r["title"]: r["id"] for r in rows}
        related = app_client.get(f"/api/documents/{by_title['a']}/related").json()
        assert [r["document_id"] for r in related] == [by_title["b"]]


def test_consent_taken_back_as_a_card_leaves_stops_it(
    library, cloud, tmp_path, monkeypatch
):
    root = tmp_path / "one"
    root.mkdir()
    (root / "One.txt").write_text("One.\n")
    source = sources.add_source(str(root), states.STANDARD, auto_organize=True)
    build = models._anthropic_model  # noqa: SLF001

    def turned_off(*given, **named):
        sources.set_switches(source["id"], auto_organize=False)
        return build(*given, **named)

    monkeypatch.setattr(models, "_anthropic_model", turned_off)
    drain()
    assert cloud.requests == []


class TestBatchBoundaries:
    def test_closing_an_attempt_comes_before_planning_again(
        self, library, cloud, tmp_path, monkeypatch
    ):
        long_ago = time.time() - batches.TOO_LATE_SECONDS - 60
        batch_id = plant_batch(tmp_path, batches.SUBMIT_UNKNOWN, marked=long_ago)
        row = row_of(batch_id)
        models.api.log_batch_attempt(
            "anthropic:claude-haiku-5-5",
            ["d"],
            models.api.Quote(HAIKU, 0.5),
            row["submission_id"],
        )
        monkeypatch.setattr(batches.models, "batches_since", lambda _model, _m: [])
        real = batches._move  # noqa: SLF001

        def crash(*_given, **_named):
            raise RuntimeError

        monkeypatch.setattr(batches, "_move", crash)
        with pytest.raises(RuntimeError):
            batches._reconcile(row)  # noqa: SLF001
        assert spend.spent_this_month() == 0.0
        monkeypatch.setattr(batches, "_move", real)
        batches._reconcile(row)  # noqa: SLF001
        assert state_of(batch_id) == batches.PLANNED

    def test_the_estimate_counts_what_else_is_waiting(
        self, library, tmp_path, monkeypatch
    ):
        root = tmp_path / "small"
        root.mkdir()
        (root / "One.txt").write_text("One.\n")
        source = sources.add_source(str(root), states.STANDARD)
        drain()
        waiting = {f"w{n}" for n in range(spend.BATCH_ABOVE + 1)}
        real = sources.waiting_for_cards
        monkeypatch.setattr(
            sources,
            "waiting_for_cards",
            lambda opting_in=None: real(opting_in) if opting_in else waiting,
        )
        assert sources.organize_estimate(source["id"])["batched"] is True

    def test_a_document_gone_private_as_it_leaves_stops_the_send(
        self, library, cloud, tmp_path, monkeypatch
    ):
        batch_id = plant_batch(tmp_path, batches.PLANNED)
        real = batches._wanted_now  # noqa: SLF001

        def goes_private(batch):
            wanted = real(batch)
            with database.write() as connection:
                connection.execute(
                    database.documents.update().values(sensitivity=states.PRIVATE)
                )
            return wanted

        monkeypatch.setattr(batches, "_wanted_now", goes_private)
        monkeypatch.setattr(batches, "plan", lambda: 0)
        batches.tick()
        assert cloud.requests == []
        assert state_of(batch_id) in (batches.PLANNED, batches.DROPPED)
        with database.read() as connection:
            outcomes = connection.execute(
                sa.select(database.egress_log.c.outcome)
            ).scalars()
            assert "attempted" not in list(outcomes)

    def test_consent_taken_back_as_it_leaves_stops_the_send(
        self, library, cloud, tmp_path, monkeypatch
    ):
        plant_batch(tmp_path, batches.PLANNED)
        real = batches._wanted_now  # noqa: SLF001

        def turned_off(batch):
            wanted = real(batch)
            with database.write() as connection:
                connection.execute(
                    database.sources.update().values(auto_organize=False)
                )
            return wanted

        monkeypatch.setattr(batches, "_wanted_now", turned_off)
        monkeypatch.setattr(batches, "plan", lambda: 0)
        batches.tick()
        assert cloud.requests == []

    def test_the_cap_is_asked_before_each_batch(
        self, library, cloud, tmp_path, monkeypatch
    ):
        plant_batch(tmp_path, batches.PLANNED)
        answers = iter([False, True])
        monkeypatch.setattr(spend, "over_cap", lambda: next(answers, True))
        monkeypatch.setattr(batches, "plan", lambda: 0)
        batches.tick()
        assert cloud.requests == []

    def test_results_settle_the_bill_once(self, library, cloud, tmp_path, monkeypatch):
        batch_id = plant_batch(tmp_path, batches.ENDED, provider="p1")
        row = row_of(batch_id)
        models.api.log_batch_attempt(
            "anthropic:claude-haiku-5-5",
            ["d"],
            models.api.Quote(HAIKU, 0.5),
            row["submission_id"],
        )
        usage = {"input_tokens": 1000, "output_tokens": 100}
        monkeypatch.setattr(
            batches.models,
            "batch_results",
            lambda _model, _p: [
                {
                    "custom_id": f"{row['submission_id']}-none",
                    "fields": None,
                    "usage": usage,
                }
            ],
        )
        batches._apply(row)  # noqa: SLF001
        batches._apply(row)  # noqa: SLF001
        with database.read() as connection:
            logged = connection.execute(sa.select(database.egress_log)).mappings().one()
        assert logged["outcome"] == "sent"
        assert logged["input_tokens"] == 1000
        assert logged["cost_usd"] == pytest.approx(
            spend.cost("anthropic:claude-haiku-5-5", 1000, 100) / 2
        )

    def test_a_batch_card_whose_consent_went_as_it_applied_isnt_kept(
        self, library, cloud, tmp_path, monkeypatch
    ):
        # Eligible when asked, then the folder turned Private
        # before the card was written.
        batch_id = plant_batch(tmp_path, batches.ENDED, provider="p1")
        row = row_of(batch_id)
        items = batches._items_of(batch_id)  # noqa: SLF001
        monkeypatch.setattr(
            batches.models,
            "batch_results",
            lambda _model, _p: [
                {
                    "custom_id": f"{row['submission_id']}-{item['document_id']}",
                    "fields": CARD,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
                for item in items
            ],
        )
        real = batches._still_wanted  # noqa: SLF001

        def asked_then_withdrawn(item):
            found = real(item)
            source_id = [s for s in sources.live() if s["name"] == "planted"][0]["id"]
            sources.set_switches(source_id, auto_organize=False)
            return found

        monkeypatch.setattr(batches, "_still_wanted", asked_then_withdrawn)
        batches._apply(row)  # noqa: SLF001
        for item in items:
            assert cards.get(item["document_id"])["written_by"] != "Claude Haiku 5.5"

    def test_one_batch_that_cant_be_reached_holds_no_other_back(
        self, library, cloud, tmp_path, monkeypatch
    ):
        # The first batch's failed poll ended the pass, so a later
        # batch, perhaps another provider's, was never polled.
        first = plant_batch(tmp_path, batches.SUBMITTED, provider="p1")
        (tmp_path / "more").mkdir()
        second = plant_batch(tmp_path / "more", batches.SUBMITTED, provider="p2")
        polled: list[str] = []

        def poll(batch):
            polled.append(batch["id"])
            if batch["id"] == first:
                raise models.CloudRefused("no answer")

        monkeypatch.setattr(batches, "_poll", poll)
        batches.tick()
        assert set(polled) == {first, second}

    def test_a_submission_never_accepted_costs_nothing(self, library):
        models.api.log_batch_attempt(
            "anthropic:claude-haiku-5-5", ["d"], models.api.Quote(HAIKU, 0.5), "s1"
        )
        models.batch_never_accepted("s1")
        assert spend.spent_this_month() == 0.0

    def test_a_document_in_a_live_batch_isnt_also_sent_alone(
        self, library, cloud, tmp_path
    ):
        plant_batch(tmp_path, batches.SUBMITTED, provider="p1")
        with database.read() as connection:
            document_id = (
                connection.execute(sa.select(database.documents.c.id)).scalars().first()
            )
        cloud.requests.clear()
        organize.run({"document_id": document_id})
        assert cloud.requests == []

    def test_a_live_batch_holds_no_new_card_back(
        self, library, cloud, tmp_path, monkeypatch
    ):
        # A batch still waiting, its provider's key since removed, counted as the
        # backlog, so a new document's card went neither alone nor in a batch.
        monkeypatch.setattr(organize, "BACKLOG_ALONE", 1)
        monkeypatch.setattr(batches, "ALONE", 1)
        plant_batch(tmp_path, batches.SUBMITTED, provider="p1")
        cloud.reply = haiku_card
        cloud.requests.clear()
        root = tmp_path / "new"
        root.mkdir()
        (root / "Three.txt").write_text("Three.\n")
        sources.add_source(str(root), states.STANDARD, auto_organize=True)
        drain()
        with database.read() as connection:
            new_id = connection.execute(
                sa.select(database.documents.c.id).where(
                    database.documents.c.file_name == "Three.txt"
                )
            ).scalar_one()
        assert cards.get(new_id)["written_by"] == "Claude Haiku 5.5"
        assert len(cloud.requests) == 1

    def test_the_estimate_leaves_out_a_live_batch_too(
        self, library, cloud, tmp_path, monkeypatch
    ):
        # The worker leaves a live batch's documents out of its backlog, so the
        # estimate's batch price must too.
        monkeypatch.setattr(spend, "BATCH_ABOVE", 1)
        plant_batch(tmp_path, batches.SUBMITTED, provider="p1")
        root = tmp_path / "new"
        root.mkdir()
        (root / "Three.txt").write_text("Three.\n")
        source = sources.add_source(str(root), states.STANDARD)
        drain()
        assert sources.organize_estimate(source["id"])["batched"] is False

    def test_the_estimate_leaves_out_cards_already_written(
        self, library, cloud, tmp_path, monkeypatch
    ):
        # The estimate counted every document in the folder, so cards written before
        # organizing was turned on showed the batch price, and an amount, for work it
        # never does.
        monkeypatch.setattr(spend, "BATCH_ABOVE", 1)
        cloud.reply = haiku_card
        root = tmp_path / "w"
        root.mkdir()
        for name in ("One", "Two", "Three"):
            (root / f"{name}.txt").write_text(f"{name}.\n")
        source = sources.add_source(str(root), states.STANDARD)
        drain()
        assert sources.organize_estimate(source["id"])["batched"] is True
        with database.read() as connection:
            rows = connection.execute(
                sa.select(
                    database.documents.c.id, database.documents.c.active_revision_id
                ).order_by(database.documents.c.file_name)
            ).all()
            left = connection.execute(
                sa.select(sa.func.sum(database.chunks.c.token_count)).where(
                    database.chunks.c.revision_id == rows[-1][1]
                )
            ).scalar_one()
        for document_id, revision_id in rows[:2]:
            assert cards.write_card(document_id, revision_id, CARD, "gemma4:26b")
        found = sources.organize_estimate(source["id"])
        assert found["batched"] is False, "one card left is no backlog"
        one = spend.card_estimate(min(left, spend.EXCERPT_TOKENS), 1, 1)
        assert found["backfill_usd"] == one["backfill_usd"]

    def test_a_document_kept_twice_in_a_folder_is_counted_once(self, library, tmp_path):
        # One document in two places in the folder had its text counted once for each
        # place.
        root = tmp_path / "twice"
        (root / "a").mkdir(parents=True)
        (root / "b").mkdir()
        # About 3,000 tokens: under half an excerpt, so counted twice it shows.
        text = "The rent is due on the first of the month. " * 200 + "\n"
        (root / "a" / "Lease.txt").write_text(text)
        (root / "b" / "Lease.txt").write_text(text)
        source = sources.add_source(str(root), states.STANDARD)
        drain()
        with database.read() as connection:
            places = connection.execute(
                sa.select(sa.func.count()).select_from(sources.document_locations)
            ).scalar_one()
            documents = connection.execute(
                sa.select(sa.func.count()).select_from(database.documents)
            ).scalar_one()
            tokens = connection.execute(
                sa.select(sa.func.sum(database.chunks.c.token_count))
            ).scalar_one()
        assert (documents, places) == (1, 2), "one document, two places"
        assert 2_000 < tokens < spend.EXCERPT_TOKENS / 2
        one = spend.card_estimate(tokens, 1, 1)
        found = sources.organize_estimate(source["id"])
        assert found["backfill_usd"] == one["backfill_usd"]

    def test_one_dropped_from_its_batch_can_go_alone(self, library, tmp_path):
        batch_id = plant_batch(tmp_path, batches.SUBMITTED, provider="p1")
        items = batches._items_of(batch_id)  # noqa: SLF001
        batches._set_item(  # noqa: SLF001
            batch_id, items[0]["document_id"], batches.ITEM_DROPPED
        )
        assert organize.in_live_batches() == {items[1]["document_id"]}
