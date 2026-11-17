"""Runs on OpenAI alone: the judge and the helper named for a run, the judge reached
through the agent service, a kept run judged again by another judge, and a run held to
the providers it names."""

import json
from pathlib import Path

import pytest

from agent import models
from backend import agent_service
from evals import cli, compare, estimate, rejudge, run, score
from evals import kept as keeping
from evals.tests.judged_runs import (
    JUDGE_SAYS,
    TERRA,
    handed,
    judged_row,
    judged_run,
    openai_judging,
)
from evals.tests.kept_runs import LOCAL, SET, answer
from evals.tests.questions import PLANNED, SOL


@pytest.fixture
def on_openai(lease, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(cli, "load_questions", lambda _path: [PLANNED])
    anthropic, openai = lease
    anthropic.requests.clear()
    openai.requests.clear()
    openai.reply = openai_judging(True)
    return anthropic, openai


def test_the_judge_is_claude_opus_unless_a_run_names_another():
    assert agent_service.judge_model() == models.JUDGE_MODEL
    with agent_service.judged_by(TERRA):
        assert agent_service.judge_model() == TERRA
    assert agent_service.judge_model() == models.JUDGE_MODEL


@pytest.mark.parametrize(
    ("model", "allowed"),
    [
        (SOL, True),
        ("openai:gpt-6-luna", True),
        ("anthropic:claude-haiku-5-5", False),
        ("anthropic:claude-opus-5-5", False),
        ("local", True),
        (LOCAL, True),
    ],
)
def test_a_run_held_to_openai_may_call_openai_and_this_mac_alone(model, allowed):
    assert agent_service.provider_allowed(model)
    with agent_service.only_providers(("openai",)):
        assert agent_service.provider_allowed(model) is allowed


def test_a_run_on_openai_alone_judges_with_openai_and_sends_nothing_else(
    on_openai, capsys
):
    anthropic, openai = on_openai
    given = ["--model", SOL, "--helper-model", "openai:gpt-6-luna"]
    given += ["--judge-model", TERRA, "--only-providers", "openai", "--limit-usd", "5"]
    given += ["--skip-hard-pages"]
    cli.main(given)  # one question may miss a target; what it sent is the point
    assert anthropic.requests == [], "nothing went to Anthropic"
    judged = [r for r in openai.requests if JUDGE_SAYS in r.raw]
    assert judged, "the judge's calls went to OpenAI"
    assert all(json.loads(r.raw)["model"] == "gpt-6.1-terra" for r in judged)
    printed = capsys.readouterr().out
    assert f"It calls {SOL} to answer, openai:gpt-6-luna to help, {TERRA} to judge" in (
        printed
    )
    (folder,) = (cli.REPORTS / cli.RUNS).iterdir()
    manifest = keeping.Kept(folder).manifest
    assert (manifest["judge_model"], manifest["helper_model"]) == (
        TERRA,
        "openai:gpt-6-luna",
    )


def test_a_run_held_to_openai_that_would_call_claude_is_refused_before_it_starts(
    on_openai,
):
    """Its helper is the vault's, Claude Haiku 5.5: nothing is sent at all."""
    anthropic, openai = on_openai
    given = ["--model", SOL, "--judge-model", TERRA, "--only-providers", "openai"]
    with pytest.raises(run.NotAllowed, match="its helper, anthropic:claude-haiku-5-5"):
        cli.main([*given, "--limit-usd", "5"])
    assert (anthropic.requests, openai.requests) == ([], [])


def test_the_judge_left_as_opus_is_refused_too(on_openai):
    anthropic, openai = on_openai
    given = ["--model", SOL, "--helper-model", "openai:gpt-6-luna"]
    with pytest.raises(run.NotAllowed, match="its judge, anthropic:claude-opus-5-5"):
        cli.main([*given, "--only-providers", "openai", "--limit-usd", "5"])
    assert (anthropic.requests, openai.requests) == ([], [])


def test_the_gate_refuses_another_providers_request_as_it_leaves(on_openai):
    """Past the start's check, as a call made by a model the run didn't name."""
    anthropic, _openai = on_openai
    with agent_service.only_providers(("openai",)), pytest.raises(models.CloudRefused):
        agent_service.judge_sentence("It is.", [], None)
    assert anthropic.requests == []


@pytest.mark.parametrize("flag", ["--compare", "--write-baseline", "--record"])
def test_what_the_spec_judges_with_opus_takes_opus(on_openai, flag):
    with pytest.raises(SystemExit):
        cli.main(["--model", SOL, "--judge-model", TERRA, flag, "--limit-usd", "5"])


def test_judged_runs_by_two_judges_compare_only_unjudged(tmp_path):
    opus = keeping.Kept(judged_run(tmp_path))
    terra = keeping.Kept(judged_run(tmp_path, judge_model=TERRA))
    found = compare.side_by_side([opus, terra])
    assert [column["judged"] for column in found["columns"]] == [False, False]
    assert "judged by different judges" in compare.table(found)


def test_a_kept_run_is_judged_again_from_its_rows_asking_only_the_judge(
    on_openai, tmp_path, monkeypatch
):
    anthropic, openai = on_openai
    openai.reply = openai_judging(False)
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    monkeypatch.setattr(rejudge, "_question", lambda _id: PLANNED)
    old = keeping.Kept(judged_run(tmp_path / "old"))
    with agent_service.judged_by(TERRA):
        new, agreement = rejudge.rejudge(old, tmp_path / "new", TERRA, 1.0)
    assert anthropic.requests == []
    assert len(openai.requests) == 2, "the two claims Opus read, and only those"
    assert (agreement["claims"], agreement["differ"]) == (2, 1)
    assert agreement["changed"] == [{"question": "q1", "claim": 0, "was": True}]
    claims = new.rows()[0]["claims"]
    assert [c.get("supported") for c in claims] == [False, False, None]
    assert new.manifest["judge_model"] == TERRA
    assert new.manifest["rejudged_from"] == old.folder.name
    assert old.rows()[0]["claims"][0]["supported"] is True, "the old run is unchanged"


def test_a_rejudged_run_counts_its_own_figures_and_writes_its_own_sheet(
    on_openai, tmp_path, monkeypatch
):
    """A re-judged run is a run of its
    own, so its report's figures section and sheet are its own, never the old run's
    or none."""
    _anthropic, openai = on_openai
    openai.reply = openai_judging(False)
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    monkeypatch.setattr(rejudge, "_question", lambda _id: PLANNED)
    old = keeping.Kept(judged_run(tmp_path / "old"))
    assert "figures" not in (old.report() or {}), "a run from before the check"
    with agent_service.judged_by(TERRA):
        new, _ = rejudge.rejudge(old, tmp_path / "new", TERRA, 1.0)
    found = (new.report() or {})["figures"]
    assert found["run"] == new.folder.name
    assert Path(found["sheet"]).name == f"{new.folder.name}.csv"
    assert Path(found["sheet"]).exists()


def test_a_run_its_judge_already_judged_isnt_judged_again(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    old = keeping.Kept(judged_run(tmp_path, judge_model=TERRA))
    with pytest.raises(rejudge.CantRejudge, match="already"):
        rejudge.checked(old, TERRA)


def test_rejudging_is_estimated_from_the_calls_the_run_made(tmp_path):
    old = keeping.Kept(judged_run(tmp_path))
    # 1,000 tokens sent at $1 a million, and at most 1,024 written at $5 a million:
    # $0.00612, to the four places every estimate gives.
    assert rejudge.expected_usd(old, TERRA) == 0.0061


def test_a_rejudged_run_keeps_its_own_calls_spending_and_limit(
    on_openai, tmp_path, monkeypatch
):
    """None of the first run's calls or spending is the new one's,
    and an estimate never learns from a run judged again."""
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    monkeypatch.setattr(rejudge, "_question", lambda _id: PLANNED)
    old = keeping.Kept(judged_run(tmp_path / "old"))
    with agent_service.judged_by(TERRA):
        new, _ = rejudge.rejudge(old, tmp_path / "new", TERRA, 2.5)
    (row,) = new.rows()
    assert {(c["purpose"], c["model"]) for c in row["calls"]} == {("eval_judge", TERRA)}
    report = new.report() or {}
    assert report["first_run"] == {"spent_usd": 0.42, "limit_usd": 1.0}
    assert report["limit_usd"] == 2.5
    assert report["spent_usd"] != 0.42
    assert new.manifest["asked"] == "asked-1"
    assert estimate.last_kept(tmp_path / "new", LOCAL, "asked-1", True) is None
    found = estimate.last_kept(tmp_path / "old", LOCAL, "asked-1", True)
    assert found is not None
    assert found.folder == old.folder, "the first run still is"


def test_a_limit_reached_partway_keeps_what_was_judged(
    on_openai, tmp_path, monkeypatch
):
    """The new run is started first and keeps each row as it's
    judged, and a limit that stops it leaves it stopped, not lost."""
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    monkeypatch.setattr(rejudge, "_question", lambda _id: PLANNED)
    asked = []

    def judge(*_given, **_named):
        asked.append(1)
        if len(asked) > 2:  # q1's two claims are judged; q2's first meets the limit
            raise models.OverLimit("This run reached its spending limit of $0.01.")
        return True

    monkeypatch.setattr(rejudge.agent_service, "judge_citation", judge)
    monkeypatch.setattr(rejudge.agent_service, "judge_sentence", judge)
    old = keeping.Kept(
        judged_run(tmp_path / "old", [judged_row("q1"), judged_row("q2")])
    )
    new, agreement = rejudge.rejudge(old, tmp_path / "new", TERRA, 0.01)
    assert new.state == keeping.STOPPED_AT_LIMIT
    first, second = new.rows()
    assert (first["id"], second["id"]) == ("q1", "q2")
    assert second["state"] == score.STOPPED, "the row it stopped, kept as stopped"
    assert agreement["claims"] == 2
    assert "Stopped at the limit" in rejudge.text_of(new, agreement)


def test_a_limit_reached_between_two_claims_of_a_row_keeps_the_first(
    on_openai, tmp_path, monkeypatch
):
    """The claim judged again before the limit keeps its new
    verdict and its call's record; the one it didn't reach loses its verdict, kept
    apart as the first judge's, so it can't pass for judged again."""
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    monkeypatch.setattr(rejudge, "_question", lambda _id: PLANNED)
    asked = []

    def judge(*_given, **_named):
        asked.append(1)
        if len(asked) > 1:
            raise models.OverLimit("This run reached its spending limit of $0.01.")
        # As a real call is recorded: outside any thread, by the judge.
        recorder = models._calls  # noqa: SLF001  the recorder this run turned on
        assert recorder is not None
        recorder.append({"thread_id": None, "purpose": "eval_judge", "model": TERRA})
        return False

    monkeypatch.setattr(rejudge.agent_service, "judge_citation", judge)
    monkeypatch.setattr(rejudge.agent_service, "judge_sentence", judge)
    old = keeping.Kept(judged_run(tmp_path / "old"))
    new, agreement = rejudge.rejudge(old, tmp_path / "new", TERRA, 0.01)
    assert new.state == keeping.STOPPED_AT_LIMIT
    (row,) = new.rows()
    assert row["state"] == score.STOPPED
    assert row["correct"] is None
    cited, uncited, by_hand = row["claims"]
    assert cited["supported"] is False, "judged again before the limit"
    assert "supported" not in uncited
    assert (uncited["not_rejudged"], uncited["first_supported"]) == (True, False)
    assert by_hand.get("by_hand") is True
    assert row["calls"] == [
        {"thread_id": None, "purpose": "eval_judge", "model": TERRA}
    ]
    assert (agreement["claims"], agreement["differ"]) == (1, 1)


def test_the_same_words_in_two_sentences_keep_their_own_citations(
    on_openai, tmp_path, monkeypatch
):
    """Each sentence's citation is judged with its own sentence's
    siblings, as the first judge was asked, never the other sentence's."""
    _anthropic, openai = on_openai
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    monkeypatch.setattr(rejudge, "_question", lambda _id: PLANNED)
    fee = "The fee is $100."
    row = answer(
        "q1",
        True,
        sentences=[{"text": f"{fee} ", "cite": [1]}, {"text": f"{fee} ", "cite": [2]}],
        citations=[{"n": 1, "title": "Gym"}, {"n": 2, "title": "Pool"}],
        claims=[
            {
                "claim": fee,
                "title": "Gym",
                "words": "$100",
                "passage": "Gym $100.",
                "supported": True,
            },
            {
                "claim": fee,
                "title": "Pool",
                "words": "$100",
                "passage": "Pool $100.",
                "supported": True,
            },
        ],
        model="gemma4:26b",
        written={
            "handed": handed(
                ("gym", "Gym", "Gym $100."), ("pool", "Pool", "Pool $100.")
            ),
            "table": None,
        },
    )
    old = keeping.Kept(judged_run(tmp_path / "old", [row]))
    with agent_service.judged_by(TERRA):
        rejudge.rejudge(old, tmp_path / "new", TERRA, 1.0)
    judged = [r.raw for r in openai.requests if JUDGE_SAYS in r.raw]
    assert len(judged) == 2
    assert all("also_cited" not in raw for raw in judged), "no sibling from elsewhere"


def test_claims_that_dont_line_up_with_their_sentences_are_refused_before_any_call(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    row = judged_row()
    row["sentences"] = row["sentences"][:1]
    old = keeping.Kept(judged_run(tmp_path / "old", [row]))
    with pytest.raises(rejudge.CantRejudge, match="don't line up"):
        rejudge.rejudge(old, tmp_path / "new", TERRA, 1.0)
