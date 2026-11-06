"""Reading a page with Claude. The stub records every byte that reaches it."""

import io
import itertools
from pathlib import Path

import anthropic
import pytest
import sqlalchemy as sa
from PIL import Image

from agent import api, models
from backend import (
    database,
    documents,
    jobs,
    keychain,
    model_catalog,
    settings,
    sources,
    spend,
    states,
)
from backend.build_mode import STUB_ANTHROPIC_ENV
from testing.pdfs import pdf_pages
from testing.queue import drain, next_job
from testing.stub_models import stub_anthropic
from worker import hard_pages
from worker import main as worker_main

TEXT_BACK = "The page as Claude read it, with marker tarragon."


def tiny_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, "PNG")
    return buffer.getvalue()


def only_document() -> dict:
    with database.read() as connection:
        row = connection.execute(sa.select(database.documents)).mappings().one()
    return dict(row)


@pytest.fixture
def cloud(monkeypatch):
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)

        def reply(_path, body):
            return {
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": [{"type": "text", "text": TEXT_BACK}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }

        stub.reply = reply
        yield stub
    keychain.delete_key()


@pytest.fixture
def pdf_three_pages() -> bytes:
    return pdf_pages("Page one text.", "Page two text.", "Page three text.")


@pytest.fixture
def standard_pdf(library, tmp_path, pdf_three_pages):
    folder = tmp_path / "w"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(pdf_three_pages)
    sources.add_source(str(folder), states.STANDARD)
    drain()
    return only_document()


def make_private(document_id: str) -> None:
    source_id = documents.locations(document_id)[0]["source_id"]
    sources.set_sensitivity(source_id, states.PRIVATE)


def logged_outcomes() -> list[str]:
    with database.read() as connection:
        return list(
            connection.execute(sa.select(database.egress_log.c.outcome)).scalars()
        )


def as_shown(page: int) -> dict:
    """A cloud read as the app sends it: the page, the model and price its cost was
    shown at, which are Settings' now, and the revision shown, the PDF's own."""
    model = spend.page_model()
    price = model_catalog.price(model) or (0.0, 0.0)
    with database.read() as connection:
        (revision,) = connection.execute(
            sa.select(database.documents.c.active_revision_id).where(
                database.documents.c.file_name.like("%.pdf")
            )
        ).scalars()
    return {
        "page": page,
        "model": model,
        "input_usd": price[0],
        "output_usd": price[1],
        "revision_id": revision,
    }


def images_in(stub) -> list[dict]:
    found = []
    for request in stub.requests:
        for message in request.body.get("messages", []):
            if isinstance(message["content"], list):
                found += [b for b in message["content"] if b["type"] == "image"]
    return found


def test_only_the_chosen_page_goes_as_an_image_with_no_tools(
    standard_pdf, cloud, app_client
):
    document = standard_pdf
    queued = app_client.post(
        f"/api/documents/{document['id']}/cloud-read-page", json=as_shown(2)
    )
    assert queued.status_code == 200
    drain()
    assert len(cloud.requests) == 1
    body = cloud.requests[0].body
    assert "tools" not in body
    assert len(images_in(cloud)) == 1
    assert body["messages"][0]["role"] == "user"
    assert "page 2" in body["messages"][0]["content"][0]["text"]
    with database.read() as connection:
        logged = connection.execute(sa.select(database.egress_log)).mappings().one()
    assert logged["outcome"] == "sent"
    assert logged["document_ids"] == [document["id"]]
    assert logged["cost_usd"] > 0
    stored = app_client.get(f"/api/documents/{document['id']}/pages/2").json()
    assert "tarragon" in " ".join(e["text"] for e in stored["elements"])


def test_a_private_document_is_refused_at_the_route(
    library, tmp_path, cloud, app_client, pdf_three_pages
):
    folder = tmp_path / "w"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(pdf_three_pages)
    sources.add_source(str(folder), states.PRIVATE)
    drain()
    document = only_document()
    url = f"/api/documents/{document['id']}/cloud-read-page"
    assert app_client.get(url).status_code == 409
    assert app_client.post(url, json=as_shown(1)).status_code == 409
    drain()
    assert cloud.requests == []
    assert (
        app_client.get(f"/api/documents/{document['id']}").json()["cloud_page_allowed"]
        is False
    )


def test_offline_is_refused_at_the_route(standard_pdf, cloud, app_client):
    settings.put(settings.OFFLINE, True)
    url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
    assert app_client.post(url, json=as_shown(1)).status_code == 409
    assert cloud.requests == []


def test_a_document_that_became_private_while_it_waited_isnt_sent(
    standard_pdf, cloud, app_client
):
    document = standard_pdf
    url = f"/api/documents/{document['id']}/cloud-read-page"
    assert app_client.post(url, json=as_shown(1)).status_code == 200
    make_private(document["id"])
    drain()
    assert cloud.requests == []
    with database.read() as connection:
        logged = connection.execute(sa.select(database.egress_log)).mappings().one()
    assert logged["outcome"] == "refused"


def test_offline_turned_on_while_it_waited_isnt_sent(standard_pdf, cloud, app_client):
    url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
    assert app_client.post(url, json=as_shown(1)).status_code == 200
    settings.put(settings.OFFLINE, True)
    drain()
    assert cloud.requests == []


def test_a_page_the_owner_didnt_ask_about_is_refused_at_the_moment_of_sending(
    standard_pdf, cloud
):
    with pytest.raises(models.CloudRefused):
        models.read_page_in_cloud(standard_pdf["id"], 3, 2, tiny_png())
    assert cloud.requests == []


def test_no_key_says_so(standard_pdf, app_client):
    url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
    response = app_client.post(url, json=as_shown(1))
    assert response.status_code == 409
    assert "key" in response.json()["detail"]


def test_the_cost_is_shown_first(standard_pdf, app_client):
    url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
    shown = app_client.get(url).json()
    assert shown["model"] == "anthropic:claude-sonnet-5-5"
    assert (shown["model_name"], shown["provider"]) == (
        "Claude Sonnet 5.5",
        "Anthropic",
    )
    assert (shown["input_usd"], shown["output_usd"]) == (2.0, 10.0)
    assert 0 < shown["cost_usd"] < 0.1


def test_with_no_key_for_its_provider_it_is_unavailable_saying_why(
    standard_pdf, cloud, app_client
):
    # The Anthropic key is ready; the page model is on OpenAI, with none.
    settings.put(settings.MODEL_PAGE, "openai:gpt-6.1-sol")
    url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
    shown = app_client.get(url).json()
    assert "needs an OpenAI key" in shown["unavailable"]
    refused = app_client.post(url, json=as_shown(1))
    assert refused.status_code == 409
    assert refused.json()["detail"] == shown["unavailable"], "the same reason"
    reread = f"/api/documents/{standard_pdf['id']}/reread-page"
    assert app_client.post(reread, json={"page": 1}).status_code == 200


def test_with_no_known_price_it_is_unavailable_saying_why(
    standard_pdf, cloud, app_client
):
    settings.put(settings.MODEL_PAGE, "openrouter:someone/no-price")
    url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
    shown = app_client.get(url).json()
    assert shown["unavailable"].startswith("No known price for")
    assert (shown["cost_usd"], shown["input_usd"]) == (None, None)
    assert app_client.post(url, json=as_shown(1)).status_code == 409
    assert cloud.requests == []


def test_rereading_a_page_stays_on_this_mac(standard_pdf, cloud, app_client):
    url = f"/api/documents/{standard_pdf['id']}/reread-page"
    assert app_client.post(url, json={"page": 1}).status_code == 200
    drain()
    assert cloud.requests == []


def test_a_page_of_a_replaced_revision_isnt_sent(
    standard_pdf, cloud, app_client, tmp_path
):
    """The owner saw the cost for this revision's page. A reindex that publishes while
    the job waits means they never saw what would go now."""
    document = standard_pdf
    url = f"/api/documents/{document['id']}/cloud-read-page"
    job_id = app_client.post(url, json=as_shown(1)).json()["job_id"]
    path = Path(documents.locations(document["id"])[0]["path"])
    path.write_bytes(pdf_pages("New page one.", "New page two.", "New page three."))
    documents.reindex(document["id"])
    worker_main.run_job(next_job((jobs.INGEST,)))
    drain()
    assert cloud.requests == []
    assert "wasn't sent" in jobs.get(job_id)["reason"]
    rows = app_client.get("/api/jobs", params={"tab": "skipped"}).json()
    assert "wasn't sent" in rows[0]["detail"]
    settings.put(settings.OFFLINE, True)
    settings.put(settings.OFFLINE, False)
    assert app_client.post(url, json=as_shown(1)).status_code == 200
    rows = app_client.get("/api/jobs", params={"tab": "skipped"}).json()
    assert rows == []


def test_a_new_revisions_page_isnt_given_the_old_revisions_job(
    standard_pdf, cloud, app_client
):
    # A read waiting for the revision before was returned for this one, and is
    # skipped, so this page was never read.
    document = standard_pdf
    url = f"/api/documents/{document['id']}/cloud-read-page"
    assert app_client.post(url, json=as_shown(1)).status_code == 200
    path = Path(documents.locations(document["id"])[0]["path"])
    path.write_bytes(pdf_pages("New page one.", "New page two.", "New page three."))
    documents.reindex(document["id"])
    worker_main.run_job(next_job((jobs.INGEST,)))
    refused = app_client.post(url, json=as_shown(1))
    assert refused.status_code == 409
    assert refused.json()["detail"] == documents.PAGE_OLDER_WAITING


def test_private_during_client_setup_stops_the_send(standard_pdf, cloud, monkeypatch):
    real = api.anthropic_key

    def turn_private():
        make_private(standard_pdf["id"])
        return real()

    monkeypatch.setattr(api, "anthropic_key", turn_private)
    with pytest.raises(models.CloudRefused):
        models.read_page_in_cloud(standard_pdf["id"], 1, 1, tiny_png())
    assert cloud.requests == []
    assert logged_outcomes() == ["refused"]


def test_private_after_the_message_is_built_stops_the_send(
    standard_pdf, cloud, monkeypatch
):
    real = models._page_messages  # noqa: SLF001

    def turn_private(page, image, provider):
        made = real(page, image, provider)
        make_private(standard_pdf["id"])
        return made

    monkeypatch.setattr(models, "_page_messages", turn_private)
    with pytest.raises(models.CloudRefused):
        models.read_page_in_cloud(standard_pdf["id"], 1, 1, tiny_png())
    assert cloud.requests == []


def test_a_job_cancelled_after_its_claim_sends_nothing(standard_pdf, cloud, app_client):
    url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
    job_id = app_client.post(url, json=as_shown(1)).json()["job_id"]
    job = next_job((jobs.HARD_PAGES,))
    assert job["id"] == job_id
    documents.cancel_job(job_id)
    hard_pages.run(job)
    assert cloud.requests == []
    assert jobs.get(job_id)["state"] == states.CANCELLED


def test_no_answer_stays_logged_at_its_estimate(standard_pdf, monkeypatch):
    """The provider may have taken a request whose answer never came."""
    keychain.set_key("test-not-a-real-key")
    monkeypatch.setenv(STUB_ANTHROPIC_ENV, "http://127.0.0.1:9")
    with pytest.raises(anthropic.APIConnectionError):
        models.read_page_in_cloud(standard_pdf["id"], 1, 1, tiny_png())
    keychain.delete_key()
    assert logged_outcomes() == ["attempted"]
    assert spend.spent_this_month() == pytest.approx(spend.page_read_estimate())


def test_a_refusal_from_the_provider_costs_nothing(standard_pdf, cloud):
    cloud.status = 400
    with pytest.raises(anthropic.APIStatusError):
        models.read_page_in_cloud(standard_pdf["id"], 1, 1, tiny_png())
    assert logged_outcomes() == ["failed"]
    assert spend.spent_this_month() == 0


def test_a_refused_page_says_why_in_the_tray(standard_pdf, cloud, app_client):
    url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
    assert app_client.post(url, json=as_shown(1)).status_code == 200
    settings.put(settings.OFFLINE, True)
    drain()
    rows = app_client.get("/api/jobs", params={"tab": "skipped"}).json()
    assert "Offline" in rows[0]["detail"]


def test_the_cost_shown_is_for_the_model_set(library, app_client):
    opus = "anthropic:claude-opus-5-5"
    documents_cost = spend.cost(opus, 1600, spend.PAGE_OUTPUT_TOKENS)
    settings.put(settings.MODEL_PAGE, "anthropic:claude-opus-5-5")
    assert spend.page_model() == "anthropic:claude-opus-5-5"
    assert spend.page_read_estimate() == pytest.approx(documents_cost)


class TestTheCostShownIsTheCostSent:
    """The page goes at the model and price its cost was shown at, or not at all."""

    def test_a_cost_shown_before_settings_changed_is_refused(
        self, standard_pdf, cloud, app_client
    ):
        url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
        before = as_shown(1)
        settings.put(settings.MODEL_PAGE, "anthropic:claude-opus-5-5")
        refused = app_client.post(url, json=before)
        assert refused.status_code == 409
        assert "changed since its cost was shown" in refused.json()["detail"]
        drain()
        assert cloud.requests == []

    def test_a_change_after_it_was_queued_doesnt_change_what_goes(
        self, standard_pdf, cloud, app_client
    ):
        url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
        assert app_client.post(url, json=as_shown(1)).status_code == 200
        settings.put(settings.MODEL_PAGE, "anthropic:claude-opus-5-5")
        drain()
        (sent,) = cloud.requests
        assert sent.body["model"] == "claude-sonnet-5-5"
        with database.read() as connection:
            logged = connection.execute(sa.select(database.egress_log)).mappings().one()
        assert logged["model"] == "anthropic:claude-sonnet-5-5"
        assert (logged["input_usd"], logged["output_usd"]) == (2.0, 10.0)

    def test_the_cost_shown_is_worked_out_at_the_rates_shown(
        self, standard_pdf, cloud, app_client, monkeypatch
    ):
        # The amount read the page model again, so a change between the reads showed
        # one model's amount with another's model and rates.
        chosen = itertools.chain(
            ["anthropic:claude-sonnet-5-5"],
            itertools.repeat("anthropic:claude-opus-5-5"),
        )
        monkeypatch.setattr(spend, "page_model", lambda: next(chosen))
        url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
        shown = app_client.get(url).json()
        assert shown["model"] == "anthropic:claude-sonnet-5-5"
        price = (shown["input_usd"], shown["output_usd"])
        assert price == model_catalog.price("anthropic:claude-sonnet-5-5")
        assert shown["cost_usd"] == pytest.approx(spend.page_read_estimate(price))

    def test_a_page_shown_from_another_revision_is_refused(
        self, standard_pdf, cloud, app_client
    ):
        # Indexing published a new revision while the page was shown.
        url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
        refused = app_client.post(url, json={**as_shown(1), "revision_id": "earlier"})
        assert refused.status_code == 409
        assert refused.json()["detail"] == documents.PAGE_REVISION_CHANGED
        drain()
        assert cloud.requests == []

    def test_a_read_waiting_at_another_price_isnt_returned_as_this_one(
        self, standard_pdf, cloud, app_client
    ):
        # The second approval got the first job, at the first price.
        url = f"/api/documents/{standard_pdf['id']}/cloud-read-page"
        first = as_shown(1)
        queued = app_client.post(url, json=first).json()["job_id"]
        assert app_client.post(url, json=first).json()["job_id"] == queued
        settings.put(settings.MODEL_PAGE, "anthropic:claude-opus-5-5")
        refused = app_client.post(url, json=as_shown(1))
        assert refused.status_code == 409
        assert refused.json()["detail"] == documents.PAGE_ALREADY_WAITING
        drain()
        (sent,) = cloud.requests
        assert sent.body["model"] == "claude-sonnet-5-5"

    def test_one_queued_before_the_cost_went_with_it_isnt_sent(
        self, standard_pdf, cloud, app_client
    ):
        # A job from before carries no model or price, and Settings may have changed
        # since its cost was shown.
        job_id = documents.queue_page_reading(standard_pdf["id"], "claude_page", 1)
        drain()
        assert cloud.requests == []
        assert (
            "before SecureFolio kept the cost it showed" in jobs.get(job_id)["reason"]
        )
        rows = app_client.get("/api/jobs", params={"tab": "skipped"}).json()
        assert "Send it again to see its cost" in rows[0]["detail"]
