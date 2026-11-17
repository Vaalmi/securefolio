"""Side by side: every run's score per measure, the floor and the gap, on the questions
every run scored the same way, from made-up kept runs."""

from pathlib import Path

import pytest

from evals import cli, compare, score
from evals import kept as keeping
from evals.tests.kept_runs import LOCAL, SET, SOL, answer, kept_run


def runs(tmp_path: Path, judged_local: bool = True) -> list[Path]:
    sol = [answer("q1", True), answer("q2", True), answer("q3", False)]
    local = [answer("q1", True), answer("q2", False), answer("q3", False)]
    return [
        kept_run(tmp_path, SOL, sol),
        kept_run(tmp_path, LOCAL, local, judged=judged_local),
    ]


def test_each_measure_has_every_runs_score_its_floor_and_gap(tmp_path):
    found = compare.side_by_side([keeping.Kept(f) for f in runs(tmp_path)])
    sol, local = found["columns"]
    assert sol["recall_at_10"] == pytest.approx(2 / 3)
    assert local["recall_at_10"] == pytest.approx(1 / 3)
    recall = found["measures"]["recall_at_10"]
    assert recall["floor"] == pytest.approx(1 / 3)
    assert recall["gap"] == pytest.approx(1 / 3)
    assert sol["answers_correct"] == pytest.approx(2 / 3)
    split = found["split"]
    assert split["told_by"] == "answers_correct"
    assert (split["all_right"], split["all_wrong"]) == (pytest.approx(1 / 3),) * 2
    assert split["split_questions"] is None, "named for the calibration half only"


def test_an_unjudged_run_has_no_judged_measures_and_says_so(tmp_path):
    found = compare.side_by_side([keeping.Kept(f) for f in runs(tmp_path, False)])
    _sol, local = found["columns"]
    assert local["judged"] is False
    assert local["citation_precision"] is None
    assert local["answers_correct"] is None
    assert found["measures"]["answers_correct"]["runs"] == 1
    assert found["split"]["told_by"] == "facts_found", "as every run can tell it"
    printed = compare.table(found)
    assert f"{LOCAL} (unjudged)" in printed
    assert f"{SOL} (judged)" in printed


def test_only_the_questions_every_run_scored_are_compared(tmp_path):
    sol = [answer("q1", True), answer("q2", True)]
    local = [answer("q1", True), answer("q2", False, state=score.FAILED)]
    folders = [kept_run(tmp_path, SOL, sol), kept_run(tmp_path, LOCAL, local)]
    found = compare.side_by_side([keeping.Kept(f) for f in folders])
    assert found["questions"] == 1
    assert [c["recall_at_10"] for c in found["columns"]] == [1.0, 1.0]


def test_citations_not_judged_are_counted_for_each_run(tmp_path):
    unread = answer("q1", True, correct=None, claims=[{"claim": "x", "by_hand": True}])
    folders = [
        kept_run(tmp_path, SOL, [unread]),
        kept_run(tmp_path, LOCAL, [answer("q1", True)]),
    ]
    found = compare.side_by_side([keeping.Kept(f) for f in folders])
    assert [c["citations_not_judged"] for c in found["columns"]] == [1, 0]


def test_an_unjudged_runs_citations_are_all_counted_not_judged(tmp_path):
    """It judged none, so none of its citations was."""
    cited = answer(
        "q1",
        True,
        sentences=[{"text": "It is.", "cite": [1, 2]}, {"text": "So.", "cite": [2]}],
        citations=[{"n": 1}, {"n": 2}],
    )
    folders = [
        kept_run(tmp_path, SOL, [answer("q1", True)]),
        kept_run(tmp_path, LOCAL, [cited], judged=False),
    ]
    found = compare.side_by_side([keeping.Kept(f) for f in folders])
    assert [c["citations_not_judged"] for c in found["columns"]] == [0, 3]


def test_the_split_questions_are_named_on_the_calibration_half(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    found = compare.side_by_side([keeping.Kept(f) for f in runs(tmp_path)])
    assert found["split"]["split_questions"] == ["q2"]
    assert "Split questions: q2" in compare.table(found)


@pytest.mark.parametrize(
    ("changed", "said"),
    [
        ({"question_set": "set-b"}, "question sets"),
        ({"scoring": "an older one"}, "scoring"),
        ({"finish": False}, "isn't a finished run"),
    ],
)
def test_runs_that_cant_compare_are_refused(tmp_path, changed, said):
    first = kept_run(tmp_path, SOL, [answer("q1", True)])
    second = kept_run(tmp_path, LOCAL, [answer("q1", True)], **changed)
    with pytest.raises(compare.CantCompare, match=said):
        compare.side_by_side([keeping.Kept(first), keeping.Kept(second)])


def test_one_run_alone_is_refused(tmp_path):
    with pytest.raises(compare.CantCompare, match="two"):
        compare.side_by_side([keeping.Kept(kept_run(tmp_path, SOL, []))])


def test_the_command_prints_the_table_without_a_vault(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SECUREFOLIO_VAULT", raising=False)
    folders = [str(folder) for folder in runs(tmp_path)]
    assert cli.main(["--side-by-side", *folders]) == 0
    printed = capsys.readouterr().out
    assert "| recall_at_10 |" in printed
    assert "| Measure |" in printed


def test_answers_correct_is_on_the_questions_every_judged_run_could_tell(tmp_path):
    # A's q2 went unjudged, so only q1 is told by both: A 1.0, B 0.0, gap 1.0.
    a = [answer("q1", True), answer("q2", True, correct=None)]
    b = [answer("q1", False, facts_found=True, rank=1), answer("q2", True)]
    folders = [kept_run(tmp_path, SOL, a), kept_run(tmp_path, LOCAL, b)]
    found = compare.side_by_side([keeping.Kept(f) for f in folders])
    assert [c["answers_correct"] for c in found["columns"]] == [1.0, 0.0]
    assert found["measures"]["answers_correct"]["gap"] == 1.0
    assert found["answers_correct_questions"] == 1


def test_with_no_judged_run_answers_correct_is_said_to_be_untold(tmp_path):
    # Three unjudged runs once printed "answers correct on the 29 of them".
    folders = [
        kept_run(tmp_path, model, [answer("q1", True)], judged=False)
        for model in (SOL, LOCAL)
    ]
    found = compare.side_by_side([keeping.Kept(f) for f in folders])
    assert found["answers_correct_questions"] == 0
    printed = compare.table(found)
    assert "No run here was judged" in printed
    assert "every judged run" not in printed
