"""Offline stops every batch read as it leaves (the privacy rules in
docs/ARCHITECTURE.md): nothing goes off this Mac, a batch's results and status
included, even when Offline is turned on as a pass runs."""

import pytest

from agent import models
from backend import settings
from testing.openai_batches import (
    LUNA,
    anthropic_stub,
    billed,
    openai_batches,
    submitted,
    the_batch,
)
from worker import batches


@pytest.fixture
def openai(library, monkeypatch):
    with openai_batches(monkeypatch) as stub:
        yield stub


@pytest.fixture
def anthropic(monkeypatch):
    with anthropic_stub(monkeypatch) as stub:
        yield stub


class TestOffline:
    def test_offline_turned_on_mid_pass_stops_the_download(self, openai, tmp_path):
        # After the batch is read, before its results are fetched.
        batch = submitted(openai, tmp_path)
        openai.side.finish(batch["provider_id"])
        batches._poll(the_batch())  # noqa: SLF001
        assert the_batch()["state"] == batches.ENDED, "polled, not yet applied"
        served = openai.reply

        def offline_after_the_batch(path: str, body: dict) -> dict | str:
            answer = served(path, body)
            if path == f"/batches/{batch['provider_id']}":
                settings.put(settings.OFFLINE, True)
            return answer

        openai.reply = offline_after_the_batch
        with pytest.raises(models.BATCH_WAITS):
            batches._apply(the_batch())  # noqa: SLF001
        assert [r.path for r in openai.requests if r.path.endswith("/content")] == []
        assert the_batch()["state"] == batches.ENDED
        assert billed()[0]["outcome"] == "attempted", "nothing settled yet"
        settings.put(settings.OFFLINE, False)
        openai.reply = served
        batches.tick()
        assert the_batch()["state"] == batches.APPLIED

    @pytest.mark.parametrize("model", [LUNA, "anthropic:claude-haiku-5-5"])
    def test_offline_stops_every_batch_read_on_either_provider(
        self, openai, anthropic, model
    ):
        settings.put(settings.OFFLINE, True)
        with pytest.raises(models.BATCH_WAITS):
            models.batch_status(model, "batch_1")
        with pytest.raises(models.BATCH_WAITS):
            models.batches_since(model, 0.0)
        with pytest.raises(models.BATCH_WAITS):
            models.batch_results(model, "batch_1")
        assert openai.requests == []
        assert anthropic.requests == []
