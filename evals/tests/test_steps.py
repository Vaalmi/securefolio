"""The step each wrong answer most likely went wrong at, from a made-up row that fails
at that step alone, and through the real graph on the stub models."""

from pathlib import Path

import pytest

from agent import citations, harness
from evals import kept as keeping
from evals import run, score, steps
from evals.tests.questions import QUESTION, SOL
from evals.tests.stub_providers import answering_with

FACT = "$299.11"
SAID = f"The pet deposit is {FACT}."


def row(**changed) -> dict:
    """A wrong answer whose every step held up to its citations."""
    base = {
        "id": "q1",
        "state": score.ANSWERED,
        "correct": False,
        "facts_found": False,
        "indexed_holds": True,
        "rank": 1,
        "handed_holds": True,
        "cards_hold": None,
        "kind": "answer",
        "thrown_away": False,
        "sentences": [{"text": SAID}],
        "written": {"text": f"{SAID} [1]", "table": None},
    }
    return {**base, **changed}


REFUSED = {
    "kind": "no_evidence",
    "sentences": [{"text": citations.NO_EVIDENCE}],
    "written": {"text": citations.NO_EVIDENCE, "table": None},
}
WRONG_FIGURE = {
    "sentences": [{"text": "It is $12.00."}],
    "written": {"text": "It is $12.00. [1]", "table": None},
}


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ({"indexed_holds": False}, steps.READING),
        ({"rank": None}, steps.SEARCH),
        ({"handed_holds": False}, steps.HANDING_OVER),
        (REFUSED, steps.ANSWER_OR_REFUSE),
        (WRONG_FIGURE, steps.FACTS),
        ({}, steps.CITATIONS),  # the right figure, the wrong page cited
        ({"facts_found": True}, steps.CITATIONS),  # judged: a citation didn't hold
        ({"indexed_holds": None}, steps.COULDNT_TELL),
    ],
)
def test_each_step_is_named_where_it_alone_fails(changed, expected):
    assert steps.step(QUESTION, row(**changed)) == expected


def test_an_answer_thrown_away_for_its_citations_counted_as_answered():
    thrown = {
        "kind": "no_evidence",
        "thrown_away": True,
        "sentences": [{"text": citations.NO_EVIDENCE}],
    }
    assert steps.step(QUESTION, row(**thrown)) == steps.CITATIONS
    wrong_figure = {**thrown, "written": {"text": "It is $12.00. [1, 2]"}}
    assert steps.step(QUESTION, row(**wrong_figure)) == steps.FACTS


def test_a_question_with_no_answer_has_only_step_four():
    none = {**QUESTION, "category": "unanswerable"}
    nothing_known = {"indexed_holds": None, "rank": None, "handed_holds": None}
    assert steps.step(none, row(**nothing_known)) == steps.ANSWER_OR_REFUSE


def test_a_list_is_told_by_its_cards_in_place_of_the_first_three():
    unknown = {"indexed_holds": None, "rank": None, "handed_holds": None}
    assert steps.step(QUESTION, row(**unknown, cards_hold=False)) == steps.CARDS
    held = row(**unknown, **WRONG_FIGURE, cards_hold=True)
    assert steps.step(QUESTION, held) == steps.FACTS


def test_a_find_question_that_cites_another_document_is_a_citation():
    find = {**QUESTION, "category": "find", "answer": None, "expected_page": None}
    find["assessed_by"] = score.BY_DOCUMENT
    assert steps.step(find, row()) == steps.CITATIONS


@pytest.mark.parametrize(
    "changed",
    [
        {"correct": True, "facts_found": True},
        {"correct": None, "facts_found": True},  # unjudged, its facts found
        {"state": score.FAILED},
        {"state": score.NOT_ASSESSED},
        {"state": score.STOPPED},
    ],
)
def test_a_row_not_known_to_be_wrong_has_no_step(changed):
    assert steps.step(QUESTION, row(**changed)) is None


def test_the_report_lists_wrong_answers_by_step():
    rows = [
        {**row(), "id": "q1", "step": steps.FACTS},
        {**row(), "id": "q2", "step": steps.FACTS},
        {**row(), "id": "q3", "step": steps.SEARCH},
        {**row(correct=True), "id": "q4", "step": None, "category": "fact"},
    ]
    for each in rows:
        each.setdefault("category", "fact")
        each.update(parts=1, uncited=0, cost_usd=0.0)
    assert score.summarize(rows, [], judge=True)["wrong_by_step"] == {
        steps.FACTS: 2,
        steps.SEARCH: 1,
    }
    listed = steps.by_step(rows)
    assert listed[steps.FACTS] == ["q1", "q2"]
    assert listed[steps.SEARCH] == ["q3"]
    assert listed[steps.READING] == []


def _row_of(report: dict) -> dict:
    (found,) = keeping.Kept(Path(report["kept"])).rows()
    return found


def test_a_wrong_figure_lands_at_facts_through_the_graph(lease, tmp_path):
    _anthropic, openai = lease
    openai.reply = answering_with("The pet deposit is $12.00. [1]")
    report, _claims = run.evaluate([QUESTION], SOL, tmp_path / "runs", limit_usd=50.0)
    found = _row_of(report)
    assert found["indexed_holds"] is True, "the lease, as indexed, holds it"
    assert found["step"] == steps.FACTS
    assert report["wrong_by_step"] == {steps.FACTS: 1}
    assert report["wrong_questions_by_step"][steps.FACTS] == ["q1"]


def test_a_fact_cut_off_before_a_refusal_lands_at_handing_over(
    lease, tmp_path, monkeypatch
):
    _anthropic, openai = lease
    openai.reply = answering_with(citations.NO_EVIDENCE)
    fit = harness._fit_evidence  # noqa: SLF001

    def cut(items, budget):
        fitted = fit(items, budget)
        for item in fitted:
            for element in item["elements"]:
                element["text"] = element["text"].replace(FACT, "")
        return fitted

    monkeypatch.setattr(harness, "_fit_evidence", cut)
    report, _claims = run.evaluate([QUESTION], SOL, tmp_path / "runs", limit_usd=50.0)
    assert _row_of(report)["step"] == steps.HANDING_OVER, "not the refusal"


def _passage(title: str, text: str, page: int = 3) -> dict:
    return {
        "title": title,
        "text": text,
        "elements": [{"text": text, "locator": {"page": page}}],
    }


def test_reading_needs_one_indexed_passage_to_hold_the_fact():
    # A fact split across two passages is held by neither, as search sees them.
    notice = {**QUESTION, "answer": "30 days", "expected_page": 1}
    split = [
        _passage("Apartment lease", "Notice period: 30", 1),
        _passage("Apartment lease", "days from receipt", 1),
    ]
    assert steps.indexed_holds(notice, split) is False
    assert steps.indexed_holds(notice, [_passage("Apartment lease", "30 days", 1)])
    pair = {
        **notice,
        "category": "multi",
        "expected_document": None,
        "passage_facts": {"A/One.pdf": "30 days"},
        "answer_facts": ["30 days"],
    }
    halves = [_passage("One", "Within 30"), _passage("One", "days of notice")]
    assert steps.indexed_holds(pair, halves) is False


def test_a_list_whose_cards_cant_be_checked_is_couldnt_tell():
    # No document's own figure is written down, so the cards can't be checked.
    listed = row(
        cards_hold=None,
        indexed_holds=False,
        written={"text": "", "table": {"rows": [], "total": 40.0, "wants_total": True}},
    )
    assert steps.step(QUESTION, listed) == steps.COULDNT_TELL


def test_wrong_private_answers_are_listed_apart_by_step():
    # A Private row's step is listed by question, apart from the main lists.
    rows = [
        {"id": "q1", "private": False, "step": steps.FACTS},
        {"id": "q25", "private": True, "step": steps.FACTS},
    ]
    listed = run._listed_by_step(rows)  # noqa: SLF001
    assert listed["wrong_questions_by_step"][steps.FACTS] == ["q1"]
    assert listed["private_wrong_questions_by_step"][steps.FACTS] == ["q25"]
