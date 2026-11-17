"""What the model was handed: whether the passages, as sent after any cut, held the
fact asked for, and, for a list or a total, whether the cards held each figure."""

import json
from pathlib import Path

import sqlalchemy as sa

from agent import harness
from backend import cards, database, sources, states
from evals import kept as keeping
from evals import run, score, steps
from evals.tests.questions import QUESTION, SOL
from evals.tests.stub_providers import judged
from testing.queue import drain

FACT = "$299.11"


def _rows(report: dict) -> list[dict]:
    return keeping.Kept(Path(report["kept"])).rows()


def test_a_fact_cut_off_in_the_fitting_was_handed_but_not_held(
    lease, tmp_path, monkeypatch
):
    fit = harness._fit_evidence  # noqa: SLF001

    def cut(items, budget):
        fitted = fit(items, budget)
        for item in fitted:
            for element in item["elements"]:
                if FACT in element["text"]:
                    element["text"] = element["text"].split(FACT)[0]
        return fitted

    report, _claims = run.evaluate([QUESTION], SOL, tmp_path / "whole", limit_usd=50.0)
    (whole,) = _rows(report)
    assert whole["handed_holds"] is True
    assert whole["cards_hold"] is None, "not a list"

    monkeypatch.setattr(harness, "_fit_evidence", cut)
    report, _claims = run.evaluate([QUESTION], SOL, tmp_path / "cut", limit_usd=50.0)
    (row,) = _rows(report)
    handed = row["written"]["handed"]
    assert any(p["title"] == "Apartment lease" for p in handed), "its passage went"
    assert row["handed_holds"] is False, "but not the fact"


POLICIES = {"Auto policy": "$989.60", "Renters policy": "$675.67"}
TOTAL = {
    "id": "q12",
    "category": "multi",
    "question": "How much did I spend on my policies?",
    "expected_document": None,
    "expected_page": None,
    "answer": None,
    "documents": [f"Finance/{title}.txt" for title in POLICIES],
    "private": False,
    "passage_facts": {f"Finance/{title}.txt": fact for title, fact in POLICIES.items()},
    "answer_facts": ["$1,665.27"],
    "assessed_by": None,
}


def _card(title: str, amount: str) -> None:
    with database.read() as connection:
        row = connection.execute(
            sa.select(database.documents).where(database.documents.c.title == title)
        ).one()
    card = {"amounts": [amount], "summary": "A policy."}
    assert cards.write_card(row.id, row.active_revision_id, card, "Test")


def _listing(path: str, body: dict) -> dict:
    """The Claude helper plans a list; everything else is judged as usual."""
    if "stands alone" not in json.dumps(body.get("system")):
        return judged(path, body)
    plan = {"query": "policy", "kind": "list", "date_from": None, "date_to": None}
    said = json.dumps({**plan, "keywords": ["policy"]})
    return {**judged(path, body), "content": [{"type": "text", "text": said}]}


def _policies(tmp_path: Path, amounts: dict[str, str]) -> None:
    root = tmp_path / "Finance"
    root.mkdir()
    for title in amounts:
        (root / f"{title}.txt").write_text(f"{title}.\n")
    sources.add_source(str(root), states.STANDARD)
    drain()
    for title, amount in amounts.items():
        _card(title, amount)


def test_a_totals_cards_held_every_figure(stubs, tmp_path):
    anthropic, _openai = stubs
    anthropic.reply = _listing
    _policies(tmp_path, POLICIES)
    report, _claims = run.evaluate([TOTAL], SOL, tmp_path / "runs", limit_usd=50.0)
    (row,) = _rows(report)
    assert row["kind"] == "list"
    assert row["written"]["table"]["total"] == 1665.27
    assert row["cards_hold"] is True
    assert row["handed_holds"] is None, "the cards stand in for what was handed"


def test_a_card_with_the_wrong_figure_didnt_hold_it(stubs, tmp_path):
    anthropic, _openai = stubs
    anthropic.reply = _listing
    _policies(tmp_path, {**POLICIES, "Renters policy": "$675.00"})
    report, _claims = run.evaluate([TOTAL], SOL, tmp_path / "runs", limit_usd=50.0)
    (row,) = _rows(report)
    assert row["cards_hold"] is False


def handed(*passages: tuple[str, str, int | None]) -> dict:
    return {
        "table": None,
        "handed": [
            {
                "title": title,
                "elements": [{"id": "e", "text": text, "locator": {"page": page}}],
            }
            for title, text, page in passages
        ],
    }


PAIR = {
    **QUESTION,
    "category": "multi",
    "expected_document": None,
    "expected_page": None,
    "answer": None,
    "documents": ["A/One.pdf", "A/Two.pdf"],
    "passage_facts": {"A/One.pdf": "$10.00", "A/Two.pdf": "$20.00"},
    "answer_facts": ["$10.00"],
}


def test_across_documents_each_documents_fact_must_be_handed():
    both = handed(("One", "It is $10.00.", 1), ("Two", "It is $20.00.", 1))
    assert steps.handed_holds(PAIR, both) is True
    crossed = handed(("One", "It is $20.00.", 1), ("Two", "It is $10.00.", 1))
    assert steps.handed_holds(PAIR, crossed) is False, "each in its own document"


def test_a_single_fact_must_be_on_its_page():
    lease = "Apartment lease"
    assert steps.handed_holds(QUESTION, handed((lease, f"It is {FACT}.", 3))) is True
    assert steps.handed_holds(QUESTION, handed((lease, f"It is {FACT}.", 2))) is False


def test_a_find_question_needs_only_its_document():
    find = {**QUESTION, "category": "find", "answer": None, "expected_page": None}
    find["assessed_by"] = "document"
    assert steps.handed_holds(find, handed(("Apartment lease", "Lease.", 1))) is True
    assert steps.handed_holds(find, handed(("Gym agreement", "Gym.", 1))) is False


def test_nothing_is_told_where_nothing_is_owed_or_written():
    refusal = {**QUESTION, "category": "unanswerable"}
    assert steps.handed_holds(refusal, handed(("Apartment lease", FACT, 3))) is None
    assert steps.handed_holds(QUESTION, None) is None
    assert steps.cards_hold(QUESTION, handed(("Apartment lease", FACT, 3))) is None


def test_a_total_the_table_doesnt_give_isnt_held():
    rows = [
        {"title": "One", "amount": 10.0, "amount_text": "$10.00"},
        {"title": "Two", "amount": 20.0, "amount_text": "$20.00"},
    ]
    total = {**PAIR, "answer_facts": ["$30.00"]}
    table = {"rows": rows, "total": 30.0, "wants_total": True}
    assert steps.cards_hold(total, {"table": table}) is True
    assert steps.cards_hold(total, {"table": {**table, "wants_total": False}}) is False
    extra = {**table, "total": 45.0}
    assert steps.cards_hold(total, {"table": extra}) is False, "a card too many"


def test_a_single_documents_facts_must_be_handed_not_just_the_document():
    # A question with `answer_facts` and no `answer` isn't a "find" question.
    facts = {**QUESTION, "answer": None, "expected_page": None}
    facts["answer_facts"] = [FACT]
    without = handed(("Apartment lease", "A residential lease.", 1))
    assert steps.handed_holds(facts, without) is False
    split = handed(("Apartment lease", "The deposit", 1), ("Apartment lease", FACT, 2))
    assert steps.handed_holds(facts, split) is True, "across the document's passages"
    assert score.in_passage(facts, _as_item("Apartment lease", "A lease.")) is False


def _as_item(title: str, text: str) -> dict:
    return {"title": title, "text": text, "elements": [{"text": text}]}


def test_a_total_is_held_only_by_the_tables_total():
    # A card of $30 can't stand in for a total of $30 when the table says $60.
    rows = [
        {"title": "One", "amount": 10.0},
        {"title": "Two", "amount": 20.0},
        {"title": "Extra", "amount": 30.0},
    ]
    total = {**PAIR, "answer_facts": ["$30.00"]}
    table = {"rows": rows, "total": 60.0, "wants_total": True}
    assert steps.cards_hold(total, {"table": table}) is False


def test_a_difference_is_the_answers_to_work_out_not_the_cards():
    # A figure that is neither a document's own nor their sum isn't the cards' to hold.
    rows = [{"title": "One", "amount": 10.0}, {"title": "Two", "amount": 25.0}]
    pair = {**PAIR, "passage_facts": {"A/One.pdf": "$10.00", "A/Two.pdf": "$25.00"}}
    table = {"rows": rows, "total": 35.0, "wants_total": True}
    assert steps.cards_hold({**pair, "answer_facts": ["$15.00"]}, {"table": table})
    summed = {**pair, "answer_facts": ["$35.00"]}
    assert steps.cards_hold(summed, {"table": table}) is True
    assert steps.cards_hold(summed, {"table": {**table, "total": 45.0}}) is False


def test_a_total_equal_to_one_documents_figure_is_still_checked():
    # r2: with charges of $10 and $0, the $10 total is also A's own figure.
    rows = [
        {"title": "One", "amount": 10.0},
        {"title": "Two", "amount": 0.0},
        {"title": "Extra", "amount": 5.0},
    ]
    zero = {**PAIR, "passage_facts": {"A/One.pdf": "$10.00", "A/Two.pdf": "$0.00"}}
    total = {**zero, "answer_facts": ["$10.00"]}
    table = {"rows": rows, "total": 15.0, "wants_total": True}
    assert steps.cards_hold(total, {"table": table}) is False
    assert steps.cards_hold(total, {"table": {**table, "total": 10.0}}) is True
    listed = {**table, "rows": rows[:2], "wants_total": False}
    assert steps.cards_hold(total, {"table": listed}) is True, "the card holds it"
