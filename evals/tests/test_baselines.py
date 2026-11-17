"""Each model's own baseline, written from a finished, judged kept run on the
calibration half, with its rows left out and Private apart."""

import json

import pytest

from evals import baselines, cli, compare, standing
from evals.tests.kept_runs import LOCAL, SET, SOL, answer, kept_run


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(baselines, "FOLDER", tmp_path / "baselines")
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    return tmp_path / "baselines"


def _with_report(run, **report):
    (run / "report.json").write_text(json.dumps(report))
    return run


def test_a_judged_run_is_its_own_models_baseline(tmp_path, folder, monkeypatch):
    monkeypatch.delenv("SECUREFOLIO_VAULT", raising=False)
    run = kept_run(tmp_path, LOCAL, [answer("q1", True)])
    report = {
        "answer_model": LOCAL,
        "answered_by": ["gemma4:26b"],
        "citation_precision": 0.9,
        "private": {"questions": 2},
        "rows": [{"id": "q1"}],
    }
    _with_report(run, **report)
    assert cli.main(["--baseline-from", str(run)]) == 0, "no vault is opened"
    (written,) = folder.iterdir()
    assert written.name == "local_gemma4_26b.json"
    baseline = json.loads(written.read_text())
    assert baseline["citation_precision"] == 0.9
    assert baseline["private"] == {"questions": 2}, "Private apart"
    assert "rows" not in baseline
    assert baseline["kept"] == run.name
    assert baseline["judge_model"] == "anthropic:claude-opus-5-5"
    assert baselines.path(SOL).name == "openai_gpt-6.1-sol.json"


def test_another_judges_baseline_is_kept_beside_opuss(tmp_path, folder):
    report = {"answer_model": LOCAL, "answered_by": ["gemma4:26b"], "rows": []}
    opus = _with_report(kept_run(tmp_path / "a", LOCAL, [answer("q1", True)]), **report)
    terra = _with_report(
        kept_run(
            tmp_path / "b",
            LOCAL,
            [answer("q1", True)],
            judge_model="openai:gpt-6.1-terra",
        ),
        **report,
    )
    assert cli.main(["--baseline-from", str(opus)]) == 0
    assert cli.main(["--baseline-from", str(terra)]) == 0
    assert sorted(p.name for p in folder.iterdir()) == [
        "local_gemma4_26b.json",
        "local_gemma4_26b.judged-by-openai_gpt-6.1-terra.json",
    ]
    beside = json.loads(next(folder.glob("*terra.json")).read_text())
    assert beside["judge_model"] == "openai:gpt-6.1-terra"


@pytest.mark.parametrize(
    ("made", "said"),
    [
        ({"judged": False}, "wasn't judged"),
        ({"question_set": "another"}, "calibration half"),
        ({"finish": False}, "isn't a finished run"),
    ],
)
def test_a_run_that_cant_be_a_baseline_is_refused(tmp_path, folder, made, said):
    run = kept_run(tmp_path, LOCAL, [answer("q1", True)], **made)
    if made.get("finish", True):
        _with_report(run, answer_model=LOCAL)
    with pytest.raises(standing.CantStand, match=said):
        baselines.write(cli.Kept(run))
    assert not folder.exists()


@pytest.mark.parametrize(
    ("model", "answered_by", "allowed"),
    [
        (SOL, [SOL], True),
        (SOL, ["gemma4:26b"], False),  # Offline's fallback answered for Sol
        (SOL, ["gemma4:26b", SOL], False),
        (SOL, [], False),
        (LOCAL, ["gemma4:26b"], True),
    ],
)
def test_only_a_run_the_model_answered_itself_is_its_baseline(
    tmp_path, folder, model, answered_by, allowed
):
    run = kept_run(tmp_path, model, [answer("q1", True)])
    private = {"answered_by": ["gemma4:26b"]}  # Private is answered on this Mac
    _with_report(run, answer_model=model, answered_by=answered_by, private=private)
    if allowed:
        assert baselines.write(cli.Kept(run)).exists()
    else:
        with pytest.raises(standing.CantStand, match="didn't write every"):
            baselines.write(cli.Kept(run))
        assert not folder.exists()
