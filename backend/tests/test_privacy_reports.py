"""A wrong-answer report is Private if its chat is, the document picked is, or any
document or memory its answer rested on is, as they are when it's saved; the form
says why."""

import pytest
import sqlalchemy as sa

from backend import database, memories, policy, reports, sources, states
from backend.tests.report_helpers import (
    answered,
    cloud_answers,
    document,
    lease_and_roof,
    report,
    saved,
)
from testing.ask_helpers import new_thread
from testing.queue import drain


@pytest.fixture
def folder(library, tmp_path):
    lease_and_roof(tmp_path)


@pytest.fixture
def cloud(monkeypatch):
    yield from cloud_answers(monkeypatch)


def test_a_report_is_private_when_its_chat_is(folder, cloud, app_client, tmp_path):
    secret = tmp_path / "s"
    secret.mkdir()
    (secret / "Diagnosis.txt").write_text("Marker zanzibar. The planted rent.\n")
    sources.add_source(str(secret), states.PRIVATE)
    drain()
    thread = new_thread(app_client)
    message = answered(app_client, thread, "When is the rent due?")
    with database.write() as connection:
        connection.execute(
            sa.update(database.threads)
            .where(database.threads.c.id == thread)
            .values(private_tainted=True)
        )
    preview = app_client.get(
        f"/api/threads/{thread}/messages/{message}/report/privacy"
    ).json()
    assert preview["why"] == "this chat is Private"
    made = report(app_client, thread, message).json()
    assert (made["private"], made["why"]) == (True, "this chat is Private")


def test_a_report_is_private_when_the_document_picked_is(
    folder, cloud, app_client, tmp_path
):
    secret = tmp_path / "s"
    secret.mkdir()
    (secret / "Diagnosis.txt").write_text("Marker zanzibar.\nThe planted rent.\n")
    sources.add_source(str(secret), states.PRIVATE)
    drain()
    thread = new_thread(app_client)
    message = answered(app_client, thread, "When is the rent due?")
    picked = document("Diagnosis")
    preview = app_client.get(
        f"/api/threads/{thread}/messages/{message}/report/privacy",
        params={"document_id": picked},
    ).json()
    assert preview["why"] == "Diagnosis is in a Private folder"
    made = report(
        app_client,
        thread,
        message,
        document_id=picked,
        place={"kind": "lines", "start": 1, "end": 2},
    )
    assert made.json()["private"] is True
    assert saved()[0]["private"] is True


def test_why_private_names_a_private_document_the_answer_rested_on(
    folder, cloud, app_client
):
    lease = document("Lease")
    with database.write() as connection:
        connection.execute(
            sa.update(database.documents)
            .where(database.documents.c.id == lease)
            .values(sensitivity=states.PRIVATE)
        )
    why = reports.why_private(False, None, {"documents": [lease], "memories": []})
    assert why == "the answer rested on Lease, which is Private"


def test_an_answer_saved_before_reports_rested_on_its_uncounted_rows_too():
    """With no saved list of what it
    rested on, a list answer's documents that couldn't be counted are named too."""
    answer = {
        "citations": [{"document_id": "cited"}],
        "table": {
            "rows": [{"document_id": "counted"}],
            "pending": [{"document_id": "uncounted"}],
        },
    }
    assert reports._rested_on(answer)["documents"] == [  # noqa: SLF001
        "cited",
        "counted",
        "uncounted",
    ]
    # One saved before its uncounted rows were saved
    # has a list of its own, and they're added to it, not left out for it.
    saved = {**answer, "rested_on": ["given", "counted"]}
    assert reports._rested_on(saved)["documents"] == [  # noqa: SLF001
        "given",
        "counted",
        "cited",
        "uncounted",
    ]


def test_why_private_names_a_private_memory_the_answer_used(library):
    memory = memories.add(
        {
            "kind": "fact",
            "text": "My rent is paid by the 3rd.",
            "embedding": None,
            "sensitivity": states.PRIVATE,
            "source_label": "A Private chat",
        }
    )
    why = reports.why_private(False, None, {"documents": [], "memories": [memory]})
    assert why == "the answer used a Private memory, “My rent is paid by the 3rd.”"


def test_a_request_resting_on_a_reports_documents_is_refused_once_one_is_private(
    folder,
):
    """While a report is asked, every
    request rests on what the report does, read fresh as each leaves."""
    lease = document("Lease")
    memory = memories.add(
        {
            "kind": "fact",
            "text": "My rent is paid by the 3rd.",
            "embedding": None,
            "sensitivity": states.STANDARD,
            "source_label": "A chat",
        }
    )
    asked = [policy.Part(policy.PartKind.QUESTION, "When is my rent due?")]
    assert policy.refusal(asked) is None
    with policy.resting_on([lease], [memory]):
        assert policy.refusal(asked) is None
        for table, row in ((database.memories, memory), (database.documents, lease)):
            with database.write() as connection:
                connection.execute(
                    sa.update(table)
                    .where(table.c.id == row)
                    .values(sensitivity=states.PRIVATE)
                )
            assert policy.refusal(asked) == policy.PRIVATE_PART
            with database.write() as connection:
                connection.execute(
                    sa.update(table)
                    .where(table.c.id == row)
                    .values(sensitivity=states.STANDARD)
                )
    assert policy.refusal(asked) is None, "held only while it holds"
