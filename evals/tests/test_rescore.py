"""A kept run scored again with today's scoring, from its own rows, as a new kept run:
the old one is left as it was, and nothing is asked."""

import json
from pathlib import Path

import pytest

from evals import cli, compare, rescore, score
from evals import kept as keeping
from evals.tests.kept_runs import LOCAL, SET, answer, kept_run
from evals.tests.questions import QUESTION

OLD = rescore.RESCORABLE[0]
REFUSED = "I couldn't find enough evidence in this scope."
HANDED = [{"title": "Apartment lease", "elements": [{"id": "e", "text": "$299.11"}]}]


def refused(**changed) -> dict:
    """An answerable question the model refused with the fact in hand, as an earlier
    scoring kept it: thrown away, and wrong at citations."""
    kept = {
        "model": "gemma4:26b",
        "rank": 1,
        "kind": "no_evidence",
        "thrown_away": True,
        "step": "citations",
        "indexed_holds": True,
        "handed_holds": True,
        "cards_hold": None,
        "written": {"text": REFUSED, "handed": HANDED, "table": None},
    }
    return answer("q1", False, **{**kept, **changed})


@pytest.fixture(autouse=True)
def calibration(monkeypatch):
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    monkeypatch.setattr(rescore, "_questions", lambda: {"q1": QUESTION})


HARD_PAGES = {
    "table_cells": 0.9,
    "chart_values": 0.8,
    "bad_pages_read": 1.0,
    "good_pages_read_again": 0.0,
}


def _old(tmp_path, rows, **manifest):
    run = kept_run(tmp_path / "old", LOCAL, rows, scoring=OLD, **manifest)
    report = {"answer_model": LOCAL, "spent_usd": 0.2, "thrown_away": 1, **HARD_PAGES}
    (run / "report.json").write_text(json.dumps(report))
    return run


def test_a_run_is_scored_again_as_a_new_kept_run(tmp_path, monkeypatch):
    monkeypatch.delenv("SECUREFOLIO_VAULT", raising=False)
    monkeypatch.setattr(cli, "REPORTS", tmp_path)
    old = _old(tmp_path, [refused()])
    before = (old / "rows.jsonl").read_text()
    assert cli.main(["--rescore", str(old)]) == 0, "no vault is opened"
    (folder,) = (tmp_path / cli.RUNS).iterdir()
    again = keeping.Kept(folder)
    (row,) = again.rows()
    assert row["thrown_away"] is False
    assert row["step"] == "answer_or_refuse"
    report = again.report()
    assert report is not None
    assert report["thrown_away"] == 0
    assert report["wrong_questions_by_step"]["answer_or_refuse"] == ["q1"]
    assert report["spent_usd"] == 0.2, "what the run spent is carried over"
    # A whole run's hard-page measurements aren't scored from its rows.
    assert {k: report[k] for k in HARD_PAGES} == HARD_PAGES
    assert again.manifest["scoring"] == score.SCORING_VERSION
    assert again.manifest["rescored_from"] == old.name
    assert again.state == keeping.FINISHED
    assert (old / "rows.jsonl").read_text() == before, "the old run is as it was"


@pytest.mark.parametrize("before", [None, "old"])
def test_a_rescored_run_counts_its_own_figures_and_writes_its_own_sheet(
    tmp_path, before
):
    """A run scored again is a run of
    its own, so its figures section and sheet are its own, never the old run's or
    none."""
    old = _old(tmp_path, [refused()])
    if before:
        report = json.loads((old / "report.json").read_text())
        report["figures"] = {"run": old.name, "sheet": str(tmp_path / "old.csv")}
        (old / "report.json").write_text(json.dumps(report))
    new = rescore.rescore(keeping.Kept(old), tmp_path / cli.RUNS)
    found = (new.report() or {})["figures"]
    assert found["run"] == new.folder.name
    assert Path(found["sheet"]).name == f"{new.folder.name}.csv"
    assert Path(found["sheet"]).exists()


def test_an_answer_thrown_away_for_its_citations_stays_thrown_away(tmp_path):
    written = {"text": "It is $299.11. [9]", "handed": HANDED, "table": None}
    old = _old(tmp_path, [refused(written=written)])
    (row,) = rescore.rescore(keeping.Kept(old), tmp_path / "runs").rows()
    assert row["thrown_away"] is True
    assert row["step"] == "citations"


@pytest.mark.parametrize(
    ("made", "said"),
    [
        ({"finish": False}, "isn't a finished run"),
        ({"question_set": "another"}, "calibration half"),
        ({"scoring": score.SCORING_VERSION}, "can't tell again"),
        ({"scoring": "2026-11-11.3"}, "can't tell again"),
    ],
)
def test_a_run_that_cant_be_scored_again_is_refused(tmp_path, made, said):
    finish = made.pop("finish", True)
    manifest = {"scoring": OLD, **made}
    run = kept_run(tmp_path / "old", LOCAL, [refused()], finish=finish, **manifest)
    with pytest.raises(rescore.CantRescore, match=said):
        rescore.rescore(keeping.Kept(run), tmp_path / "runs")
    assert not (tmp_path / "runs").exists()
