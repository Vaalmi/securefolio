""" "This was wrong": a report is saved as
a question in the owner's own set, in the vault's evals folder, never in the repo
and never sent anywhere, with the place a later answer must cite, its earlier
questions, and whether it's Private."""

import contextlib
import dataclasses
import shutil
import threading

import pytest

from agent import harness
from backend import reports, sources, states, vault
from backend.tests.report_helpers import (
    DATA,
    REPO,
    answered,
    cloud_answers,
    document,
    lease_and_roof,
    report,
    saved,
)
from testing.ask_helpers import ask, new_thread
from testing.queue import drain


@pytest.fixture
def folder(library, tmp_path):
    lease_and_roof(tmp_path)


@pytest.fixture
def cloud(monkeypatch):
    yield from cloud_answers(monkeypatch)


def test_a_report_is_saved_in_the_vaults_own_set_and_sent_nowhere(
    folder, cloud, app_client
):
    thread = new_thread(app_client)
    message = answered(app_client, thread, "When is the rent due?")
    sent = len(cloud.requests)
    made = report(
        app_client,
        thread,
        message,
        document_id=document("Lease"),
        place={"kind": "lines", "start": 2, "end": 3},
        fact="$2,350",
        note="It said weekly.",
    )
    assert made.status_code == 200
    assert made.json()["private"] is False
    assert len(cloud.requests) == sent, "a report sends nothing"
    path = reports.set_path()
    assert path == vault.root() / "evals" / "my_questions.json"
    assert not path.resolve().is_relative_to(REPO.resolve())
    (row,) = saved()
    assert row["question"] == "When is the rent due?"
    assert row["earlier"] == []
    assert row["expected_document_id"] == document("Lease")
    assert row["expected_place"] == {"kind": "lines", "start": 2, "end": 3}
    assert row["answer_facts"] == ["$2,350"]
    assert row["note"] == "It said weekly."
    assert row["reported"]["message"] == message


def test_a_follow_ups_report_keeps_the_questions_before_it(folder, cloud, app_client):
    thread = new_thread(app_client)
    answered(app_client, thread, "When is the rent due?")
    message = answered(app_client, thread, "And how much is it?")
    roof = document("Roof")
    made = report(
        app_client, thread, message, document_id=roof, place={"kind": "page", "page": 1}
    )
    assert made.status_code == 200
    (row,) = saved()
    assert row["question"] == "And how much is it?"
    assert row["earlier"] == ["When is the rent due?"]


def test_it_isnt_in_my_library_is_a_question_to_refuse(folder, cloud, app_client):
    thread = new_thread(app_client)
    message = answered(app_client, thread, "What is my pension?")
    made = report(app_client, thread, message, fact="It's with the old firm.")
    assert made.status_code == 200
    (row,) = saved()
    assert row["category"] == "unanswerable"
    assert row["expected_document_id"] is None
    assert row["answer_facts"] == [], "a question to refuse owes no fact"
    assert row["fact_noted"] == "It's with the old firm."


@pytest.mark.parametrize(
    ("said", "says"),
    [
        ({"place": {"kind": "page", "page": 9}}, "has no page 9"),
        ({"place": None}, "Say which page"),
        ({"place": {"kind": "lines", "start": 1, "end": 2}}, "Say which page"),
    ],
)
def test_a_place_the_document_hasnt_is_refused(folder, cloud, app_client, said, says):
    thread = new_thread(app_client)
    message = answered(app_client, thread, "What does the roof need?")
    made = report(app_client, thread, message, document_id=document("Roof"), **said)
    assert made.status_code == 422
    assert says in made.json()["detail"]
    assert not reports.set_path().exists()


@pytest.mark.parametrize(
    "place",
    [
        {"kind": "lines", "start": 2.5, "end": 3},
        {"kind": "lines", "start": "2", "end": 3},
        {"kind": "lines", "start": 3, "end": 2},
    ],
)
def test_lines_that_arent_whole_or_in_order_are_refused(
    folder, cloud, app_client, place
):
    thread = new_thread(app_client)
    message = answered(app_client, thread, "When is the rent due?")
    made = report(
        app_client, thread, message, document_id=document("Lease"), place=place
    )
    assert made.status_code == 422, "never read as another line"
    assert not reports.set_path().exists()


def test_a_sheets_cells_must_be_on_the_sheet(library, cloud, app_client, tmp_path):
    root = tmp_path / "f"
    root.mkdir()
    shutil.copy(DATA / "Finance" / "Household budget.xlsx", root / "Budget.xlsx")
    sources.add_source(str(root), states.STANDARD)
    drain()
    budget = document("Budget")
    sheet = reports.elements.sheets(budget)[0]["sheet"]
    columns, rows = reports._sheet_extent(budget, sheet)  # noqa: SLF001
    assert columns > 1
    assert rows > 1
    thread = new_thread(app_client)
    message = answered(app_client, thread, "What did I budget?")

    def said(cells: str) -> int:
        place = {"kind": "cells", "sheet": sheet, "cells": cells}
        return report(
            app_client, thread, message, document_id=budget, place=place
        ).status_code

    assert said("A0") == 422, "rows start at 1"
    assert said(f"A{rows + 1}") == 422
    assert said("A1:B2:C3") == 422
    assert said(f"A1:{'A' * 3}1") == 422, "past its last column"
    assert said("B2:A1") == 200


def test_two_reports_saved_at_once_are_both_kept(
    folder, cloud, app_client, monkeypatch
):
    """Each save reads the whole set and writes it back: two at once, each having
    read the set before the other wrote, would leave one out."""
    thread = new_thread(app_client)
    first = answered(app_client, thread, "When is the rent due?")
    second = answered(app_client, thread, "And how much is it?")
    read = reports._saved  # noqa: SLF001
    both = threading.Barrier(2, timeout=1)

    def slow():
        found = read()
        # The other save reads too, if it can; it can't, one save being made at
        # a time.
        with contextlib.suppress(threading.BrokenBarrierError):
            both.wait()
        return found

    monkeypatch.setattr(reports, "_saved", slow)
    made: list[object] = []
    nothing = reports.Report(None, None, None, None)

    def save(message: str) -> None:
        try:
            made.append(reports.save(thread, message, nothing)["id"])
        except Exception as failed:  # noqa: BLE001
            made.append(failed)

    workers = [threading.Thread(target=save, args=(m,)) for m in (first, second)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert all(isinstance(m, str) for m in made), made
    assert {row["id"] for row in saved()} == set(made)
    assert {row["reported"]["message"] for row in saved()} == {first, second}


def test_lines_past_the_end_are_refused(folder, cloud, app_client):
    thread = new_thread(app_client)
    message = answered(app_client, thread, "When is the rent due?")
    made = report(
        app_client,
        thread,
        message,
        document_id=document("Lease"),
        place={"kind": "lines", "start": 2, "end": 40},
    )
    assert made.status_code == 422


def test_the_chat_says_which_answers_are_reported(folder, cloud, app_client):
    thread = new_thread(app_client)
    first = answered(app_client, thread, "When is the rent due?")
    second = answered(app_client, thread, "And how much is it?")
    assert report(app_client, thread, first).status_code == 200
    shown = app_client.get(f"/api/threads/{thread}").json()["messages"]
    said = {m["id"]: m.get("reported") for m in shown if m["role"] == "assistant"}
    assert said == {first: True, second: False}


def test_a_document_not_in_the_library_and_a_second_report_are_refused(
    folder, cloud, app_client
):
    thread = new_thread(app_client)
    message = answered(app_client, thread, "When is the rent due?")
    unknown = report(app_client, thread, message, document_id="0" * 32)
    assert unknown.status_code == 404
    assert report(app_client, thread, message).status_code == 200
    again = report(app_client, thread, message)
    assert again.status_code == 409
    assert "reported already" in again.json()["detail"]


def test_passages_found_arent_an_answer_to_report(
    folder, cloud, app_client, monkeypatch
):
    routed = harness.api.route

    def blocked(*given, **named):
        return dataclasses.replace(routed(*given, **named), blocked="No model now.")

    monkeypatch.setattr(harness.api, "route", blocked)
    thread = new_thread(app_client)
    found = ask(app_client, thread, "When is the rent due?")["messages"][-1]
    assert found["answer"]["kind"] == "passages"
    made = report(app_client, thread, found["id"])
    assert made.status_code == 404
    assert not reports.set_path().exists()


def test_an_answer_saves_what_it_rested_on_and_its_memories(folder, cloud, app_client):
    thread = new_thread(app_client)
    found = ask(app_client, thread, "When is the rent due?")["messages"][-1]
    assert found["answer"]["rested_on"], "the documents it was given"
    assert found["answer"]["memory_ids"] == []


def test_the_api_names_each_kind_of_place(app_client):
    """The report's place is a union of the kinds
    `_checked_place` takes, told apart by `kind`, not any dictionary."""
    schema = app_client.app.openapi()
    body = schema["components"]["schemas"]["ReportBody"]["properties"]["place"]
    union = next(s for s in body["anyOf"] if "oneOf" in s)
    assert set(union["discriminator"]["mapping"]) == set(reports.CHECKS)


@pytest.mark.parametrize(
    "place",
    [
        {"kind": "page", "page": 1, "sheet": "Bills"},
        {"kind": "page", "page": True},
    ],
)
def test_a_place_in_no_kinds_shape_is_refused(folder, cloud, app_client, place):
    thread = new_thread(app_client)
    message = answered(app_client, thread, "What does the roof need?")
    made = report(
        app_client, thread, message, document_id=document("Roof"), place=place
    )
    assert made.status_code == 422
    assert not reports.set_path().exists()
