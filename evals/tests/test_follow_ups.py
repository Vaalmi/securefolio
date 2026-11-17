"""A follow-up is asked as the second turn of its first question's thread, searched by
its stand-alone wording, and scored apart; the set's hash stays its first questions'."""

import contextlib
import json
from pathlib import Path

from agent import harness
from evals import cli, run, score, search_parts
from evals import kept as keeping
from evals.tests.questions import QUESTION, SOL

FOLLOW_UP = {
    **QUESTION,
    "id": "f1",
    "category": "fact",
    "question": "When does it end?",
    "expected_page": 1,
    "answer": "August 3, 2027",
    "follows": "q1",
    "standalone": "When does my apartment lease end?",
}


def test_a_follow_up_is_the_next_turn_of_its_first_questions_thread(lease, tmp_path):
    report, _claims = run.evaluate(
        [QUESTION, FOLLOW_UP], SOL, tmp_path / "runs", limit_usd=50.0
    )
    first, follow_up = keeping.Kept(Path(report["kept"])).rows()
    assert follow_up["follows"] == "q1"
    assert follow_up["thread_id"] == first["thread_id"], "the same thread"
    assert follow_up["state"] == score.ANSWERED
    assert report["questions"] == 1, "the main scores are the first questions'"
    assert report["follow_ups"]["questions"] == 1, "the follow-up scored apart"
    assert report["question_set"] == score.set_hash([QUESTION])
    assert "f1" not in sum(report["wrong_questions_by_step"].values(), [])


def test_a_follow_up_is_searched_by_its_stand_alone_wording(lease, monkeypatch):
    searched = []
    real = search_parts.search.retrieve
    monkeypatch.setattr(
        search_parts.search,
        "retrieve",
        lambda words, *given, **named: (
            searched.append(words) or real(words, *given, **named)
        ),
    )
    search_parts.rank(FOLLOW_UP, search_parts.WHOLE)
    assert searched == ["When does my apartment lease end?"]


def test_a_follow_up_whose_first_question_failed_isnt_asked(
    lease, monkeypatch, tmp_path
):
    asked = []

    def timed_out(_client, question, thread=None):
        asked.append((question["id"], thread))
        return {"thread_id": "t", "times": dict(run._NO_TIMES), "timed_out": True}  # noqa: SLF001

    monkeypatch.setattr(run, "_ask", timed_out)
    report, _claims = run.evaluate(
        [QUESTION, FOLLOW_UP], SOL, tmp_path / "runs", limit_usd=50.0
    )
    assert asked == [("q1", None)], "the follow-up never went"
    rows = keeping.Kept(Path(report["kept"])).rows()
    assert [row["state"] for row in rows] == [score.FAILED, score.FAILED]
    assert report["follow_ups"][score.FAILED] == 1


def test_first_only_leaves_the_follow_ups_out(library, tmp_path, monkeypatch):
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([QUESTION, FOLLOW_UP]))
    monkeypatch.setattr(run, "QUESTIONS", questions)
    given: list[list[str]] = []

    def expected_cost(asked, *_rest):
        given.append([q["id"] for q in asked])
        raise SystemExit(0)

    monkeypatch.setattr(run, "expected_cost", expected_cost)
    for flags, ids in (([], ["q1", "f1"]), (["--first-only"], ["q1"])):
        with contextlib.suppress(SystemExit):
            cli.main(flags)
        assert given.pop() == ids


def test_a_follow_up_that_fails_never_takes_its_first_turns_answer(
    lease, tmp_path, monkeypatch
):
    # The follow-up's own run fails before it writes a turn.
    fit = harness._fit_evidence  # noqa: SLF001
    calls = []

    def second_fails(items, budget):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("the follow-up's run fails")
        return fit(items, budget)

    monkeypatch.setattr(harness, "_fit_evidence", second_fails)
    report, _claims = run.evaluate(
        [QUESTION, FOLLOW_UP], SOL, tmp_path / "runs", limit_usd=50.0
    )
    first, follow_up = keeping.Kept(Path(report["kept"])).rows()
    assert first["state"] == score.ANSWERED
    assert follow_up["state"] == score.FAILED, "not the first turn's answer"
    assert follow_up["kind"] is None
