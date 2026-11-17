"""What's measured: facts found by code apart from answers correct, an unjudged report
without the judged measures, citations the judge couldn't read counted apart, "find"
questions assessed by the document, and the page a single fact must open."""

import json
from pathlib import Path

from data.build import library
from evals import run, score
from evals.tests.questions import QUESTION, SOL

CALIBRATION = Path(__file__).parents[2] / "data/evals/calibration/questions.json"
LEASE = "Apartment lease"


def cited(page: int, title: str = LEASE) -> dict:
    return {
        "kind": "answer",
        "parts": [{"text": "The pet deposit is $299.11.", "cite": [1]}],
        "citations": [{"n": 1, "title": title, "locator": {"page": page}}],
    }


FIND = {
    **QUESTION,
    "id": "q2",
    "category": "find",
    "question": "Find my apartment lease",
    "expected_page": None,
    "answer": None,
    "assessed_by": "document",
}
OWED_NOTHING = {**FIND, "id": "q3", "assessed_by": None}


def test_a_single_fact_is_found_only_where_a_citation_opens_its_page():
    assert run._facts_found(QUESTION, cited(3)) is True  # noqa: SLF001
    assert run._facts_found(QUESTION, cited(2)) is False, "the right words, wrong page"  # noqa: SLF001
    assert run._facts_found(QUESTION, cited(3, "Car warranty booklet")) is False  # noqa: SLF001


def test_a_find_question_is_found_by_citing_its_document():
    assert run._facts_found(FIND, cited(1)) is True  # noqa: SLF001
    assert run._facts_found(FIND, cited(1, "Gym agreement")) is False  # noqa: SLF001


def test_a_question_owed_nothing_is_never_counted_right():
    assert score.assessable(OWED_NOTHING) is False
    assert run._facts_found(OWED_NOTHING, cited(1)) is None  # noqa: SLF001
    assert run._state(OWED_NOTHING, cited(1)) == score.NOT_ASSESSED  # noqa: SLF001
    assert run.correct(OWED_NOTHING, None, True, []) is None
    row = {
        "category": "find",
        "state": score.NOT_ASSESSED,
        "rank": 1,
        "facts_found": None,
        "correct": None,
        "parts": 1,
        "uncited": 0,
        "cost_usd": 0.0,
    }
    missed = {
        **row,
        "state": score.ANSWERED,
        "rank": None,
        "facts_found": False,
        "correct": False,
    }
    found = score.summarize([row, missed], [])
    assert found["answers_correct"] == 0.0, "not counted right"
    assert found["facts_found"] == 0.0
    assert found["recall_at_10"] == 0.0, "its rank enters no share"
    assert found["mrr"] == 0.0
    assert found[score.NOT_ASSESSED] == 1
    assert found["questions"] == 2


def test_a_question_owed_nothing_is_never_judged(lease):
    report, _claims = run.evaluate([QUESTION, OWED_NOTHING], SOL, limit_usd=50.0)
    rows = {row["id"]: row for row in report["rows"]}
    assert rows["q3"]["state"] == score.NOT_ASSESSED
    assert rows["q3"]["claims"] == [], "never sent to the judge"
    assert not any(c["purpose"] == "eval_judge" for c in rows["q3"]["calls"])
    assert rows["q1"]["claims"], "the question owed a fact was judged"
    assert report["citations_judged"] == len(rows["q1"]["claims"])


def test_facts_found_and_correct_are_kept_apart():
    # The facts are there, but the judge found the sentence unsupported.
    assert run.correct(QUESTION, True, False, []) is False
    assert run.correct(QUESTION, True, True, []) is True
    assert run.correct(QUESTION, True, None, []) is None, "unjudged"


def test_a_citation_the_judge_couldnt_read_is_counted_apart(monkeypatch):
    # One citation is read and supported; the judge is refused the other.
    answer = {
        "kind": "answer",
        "thread_id": "t",
        "parts": [{"text": "It is $299.11.", "cite": [1, 2]}],
        "citations": [
            {
                "n": 1,
                "title": LEASE,
                "quote": "$299.11",
                "document_id": "a",
                "revision_id": "r",
                "element_id": "e",
                "sensitivity": "standard",
            },
            {
                "n": 2,
                "title": "Notes",
                "quote": "x",
                "document_id": "b",
                "revision_id": "r",
                "element_id": "e",
                "sensitivity": "standard",
            },
        ],
    }
    monkeypatch.setattr(run, "_passage", lambda c: c.get("quote", ""))

    def judge(_sentence, target, _others, _thread, **_named):
        if target.title == "Notes":
            raise run.models.CloudRefused("refused")
        return True

    monkeypatch.setattr(run.models, "judge_citation", judge)
    claims, verdicts, _sound = run._judged(answer)  # noqa: SLF001
    row = {
        "category": "fact",
        "rank": 1,
        "facts_found": True,
        "correct": run.correct(QUESTION, True, True, claims),
        "parts": 1,
        "uncited": 0,
        "cost_usd": 0.0,
        "claims": claims,
    }
    found = score.summarize([row], verdicts)
    assert found["citations_judged"] == 1
    assert found["citation_precision"] == 1.0, "neither numerator nor denominator"
    assert found["citations_not_judged"] == 1
    assert row["correct"] is None, "not every citation was judged"


def test_an_unjudged_run_reports_what_code_can_tell(lease):
    anthropic, _openai = lease
    anthropic.requests.clear()
    refusal = {**QUESTION, "id": "q4", "category": "unanswerable", "answer": None}
    report, claims = run.evaluate(
        [QUESTION, FIND, refusal], SOL, judge=False, limit_usd=50.0
    )
    assert claims == []
    assert not any(r.body.get("model") == "claude-opus-5-5" for r in anthropic.requests)
    assert report["judged"] is False
    assert "answers_correct" not in report
    assert "citation_precision" not in report
    for measure in (
        "recall_at_10",
        "mrr",
        "facts_found",
        "unanswerable",
        "answerable_refused",
        "thrown_away",
    ):
        assert measure in report, measure
    assert all(row["correct"] is None for row in report["rows"])
    assert {row["facts_found"] for row in report["rows"]} <= {True, False}
    named = score.misses(report, whole=False)
    assert not any(name in m for m in named for name in score.JUDGED)


def test_find_questions_say_they_are_assessed_by_the_document(tmp_path):
    questions = json.loads(CALIBRATION.read_text())
    finds = [q for q in questions if q["category"] == "find"]
    assert len(finds) == 10
    assert all(q["assessed_by"] == score.BY_DOCUMENT for q in finds)
    assert all(score.assessable(q) for q in questions), (
        "every question says what's owed"
    )
    made = tmp_path / "questions.json"
    library.build_held_out(tmp_path / "library", 7, made)
    held_out = json.loads(made.read_text())
    assert all(
        q["assessed_by"] == score.BY_DOCUMENT
        for q in held_out
        if q["category"] == "find"
    ), "the generator writes it too"
