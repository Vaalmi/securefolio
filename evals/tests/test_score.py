"""The gate itself, on made-up reports."""

import json

import pytest

from evals import cli, score


def report(**changes) -> dict:
    return {
        "question_set": "same",
        "recall_at_10": 0.95,
        "citation_precision": 0.97,
        "unanswerable": 0.92,
        "table_cells": 0.98,
        "chart_values": 1.0,
        "bad_pages_read": 1.0,
        "good_pages_read_again": 0.0,
        **changes,
    }


def row(category="fact", rank: int | None = 1, correct=True, parts=2, uncited=0):
    return {
        "category": category,
        "rank": rank,
        "correct": correct,
        "parts": parts,
        "uncited": uncited,
        "cost_usd": 0.01,
    }


def test_a_run_lower_than_the_baseline_fails_even_above_target(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(report()))
    lower = report(recall_at_10=0.93)
    assert score.misses(lower) == []
    assert cli.check(lower, True, baseline) == ["recall_at_10 fell from 0.950 to 0.930"]
    assert cli.check(report(citation_precision=0.96), True, baseline)
    assert cli.check(report(), True, baseline) == []
    assert cli.check(lower, False, baseline) == []


def test_a_measure_that_didnt_run_is_a_miss():
    partial = report()
    del partial["chart_values"]
    assert "chart_values wasn't measured" in score.misses(partial)
    assert score.misses(partial, whole=False) == []
    del partial["good_pages_read_again"]
    assert "good_pages_read_again wasn't measured" in score.misses(partial)


def test_a_different_question_set_cant_compare():
    found = score.regressions(report(question_set="other"), report())
    assert "isn't the baseline's" in found[0]


@pytest.mark.parametrize(
    ("change", "named"),
    [
        ({"recall_at_10": 0.89}, "recall_at_10"),
        ({"citation_precision": 0.94}, "citation_precision"),
        ({"unanswerable": 0.5}, "unanswerable"),
        ({"table_cells": 0.9}, "table_cells"),
        ({"chart_values": 0.8}, "chart_values"),
        ({"bad_pages_read": 0.9}, "bad_pages_read"),
        ({"good_pages_read_again": 0.06}, "good_pages_read_again"),
    ],
)
def test_each_target_is_held(change, named):
    assert named in score.misses(report(**change))[0]


def test_the_summary_is_aggregates_only():
    rows = [
        row(rank=2),
        row(rank=None, correct=False, uncited=1),
        row(category="near_miss", rank=None),
        row(category="unanswerable", rank=None, correct=False),
    ]
    found = score.summarize(rows, [True, True, False, True])
    assert found["recall_at_10"] == 0.5
    assert found["mrr"] == 0.25
    assert found["citation_precision"] == 0.75
    assert found["coverage"] == 7 / 8
    assert found["answers_correct"] == 0.5
    assert found["unanswerable"] == 0.5
    assert "rows" not in found


@pytest.mark.parametrize(
    ("text", "wanted", "expected"),
    [
        ("The deposit is $299.11 monthly.", "$299.11", True),
        ("The deposit is $1,299.11.", "$299.11", False),
        ("It shows 21 for Survey 0.", "21", True),
        ("It shows 210 for Survey 0.", "21", False),
        ("Policy POL-928046 applies.", "pol-928046", True),
    ],
)
def test_an_answer_is_found_only_whole(text, wanted, expected):
    assert score.holds(text, wanted) is expected


def test_the_set_hash_follows_the_questions():
    one = [{"id": "q1", "question": "A?"}]
    two = [{"id": "q1", "question": "B?"}]
    assert score.set_hash(one) != score.set_hash(two)


def test_a_question_set_of_your_own_needs_only_its_own_fields(tmp_path):
    mine = tmp_path / "mine.json"
    mine.write_text(
        json.dumps(
            [
                {
                    "id": "m1",
                    "category": "fact",
                    "question": "What is my rent?",
                    "expected_document": "Home/Lease.pdf",
                    "answer": "$100",
                }
            ]
        )
    )
    found = cli.load_questions(mine)
    assert found[0]["answer"] == "$100"
    assert found[0]["documents"] == []
    assert found[0]["private"] is False
    assert score.set_hash(found)


def own_set(tmp_path):
    mine = tmp_path / "mine.json"
    mine.write_text("[]")
    return mine


def test_a_set_of_your_own_writes_only_into_your_vault(tmp_path, monkeypatch):
    vault, checkout = tmp_path / "vault", tmp_path / "checkout"
    monkeypatch.setenv("SECUREFOLIO_VAULT", str(vault))
    monkeypatch.setattr(cli, "REPORTS", checkout)
    monkeypatch.setattr(
        cli.run, "evaluate", lambda _q, _model=None: (report(), [{"passage": "mine"}])
    )
    monkeypatch.setattr(cli.run, "sample", lambda claims, _: claims)
    cli.main(["--questions", str(own_set(tmp_path)), "--sample-citations", "1"])
    assert "mine" in (vault / "evals" / "claims.json").read_text()
    assert (vault / "evals" / "citations_to_check.json").exists()
    assert (vault / "evals" / "last.json").exists()
    assert not checkout.exists()


def test_a_set_of_your_own_stays_out_of_the_repo(tmp_path, monkeypatch):
    mine = own_set(tmp_path)
    monkeypatch.delenv("SECUREFOLIO_VAULT", raising=False)
    with pytest.raises(SystemExit, match="needs SECUREFOLIO_VAULT"):
        cli.own_reports(mine)
    monkeypatch.setenv("SECUREFOLIO_VAULT", str(cli.REPO / ".vault-mine"))
    with pytest.raises(SystemExit, match="inside the repo"):
        cli.own_reports(mine)
    monkeypatch.setenv("SECUREFOLIO_VAULT", str(tmp_path / "vault"))
    with pytest.raises(SystemExit, match="inside the repo"):
        cli.own_reports(cli.REPO / "mine.json")
    assert cli.own_reports(mine) == tmp_path / "vault" / "evals"


@pytest.mark.parametrize("flag", ["--compare", "--write-baseline"])
def test_a_set_of_your_own_never_touches_the_baseline(tmp_path, monkeypatch, flag):
    monkeypatch.setenv("SECUREFOLIO_VAULT", str(tmp_path / "vault"))
    with pytest.raises(SystemExit):
        cli.main(["--questions", str(own_set(tmp_path)), flag])
