"""Scores where you choose a model: a cloud model's from a finished, judged kept run
into `backend/model_measured.json`, a local model's into the library named on the
command line, tied to its build. Neither asks a model, and each refuses a run as
`--baseline-from` does."""

import json
import shutil
from pathlib import Path

import pytest

from backend import model_catalog
from evals import cli, compare, measured, standing
from evals.tests.kept_runs import LOCAL, SET, SOL, answer, kept_run

BUILD = "48eb98ec778c163d87f69d735fcdd79e345063af9af4685613fd018ae5debf5e"
REPORT = {
    "questions": 60,
    "answers_correct": 0.9556,
    "citation_precision": 0.9625,
    "facts_found": 0.98,
    "unanswerable": 0.8,
    "answerable_refused": 0.0,
    "recall_at_10": 0.98,
    "private": {"answered_by": ["gemma4:26b"]},
    "rows": [{"id": "q1"}],
}


@pytest.fixture(autouse=True)
def calibration(monkeypatch):
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)


@pytest.fixture
def measured_file(tmp_path, monkeypatch):
    copy = tmp_path / "model_measured.json"
    shutil.copy(model_catalog.MEASURED, copy)
    monkeypatch.setattr(model_catalog, "MEASURED", copy)
    return copy


@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "Library"
    folder.mkdir()
    (folder / "library.db").write_bytes(b"")
    return folder


def finished(root: Path, model: str, writer: str, **manifest) -> Path:
    run = kept_run(root, model, [answer("q1", True)], **manifest)
    report = {**REPORT, "answer_model": model, "answered_by": [writer]}
    if not manifest.get("judged", True):
        report = {
            k: v
            for k, v in report.items()
            if k not in ("answers_correct", "citation_precision")
        }
        report["judged"] = False
    (run / "report.json").write_text(json.dumps(report))
    manifest_file = run / "manifest.json"
    written = json.loads(manifest_file.read_text())
    written["ended_at"] = "2026-11-14T17:39:45+00:00"
    manifest_file.write_text(json.dumps(written))
    return run


def test_a_cloud_models_scores_are_recorded_from_its_kept_run(
    tmp_path, measured_file, monkeypatch
):
    monkeypatch.delenv("SECUREFOLIO_VAULT", raising=False)
    shipped = Path(model_catalog.__file__).with_name("model_measured.json")
    before = shipped.read_text()
    run = finished(tmp_path, "openai:gpt-6-luna", "openai:gpt-6-luna")
    assert cli.main(["--record-from", str(run)]) == 0, "no vault is opened"
    assert shipped.read_text() == before, "a test writes only its copy"
    saved = json.loads(measured_file.read_text())["models"]["openai:gpt-6-luna"]
    assert saved == {
        "measured_on": "2026-11-14",
        "questions": 60,
        "judged": True,
        "judge_model": "anthropic:claude-opus-5-5",
        "answers_correct": 0.9556,
        "citation_precision": 0.9625,
        "facts_found": 0.98,
        "unanswerable": 0.8,
        "answerable_refused": 0.0,
        "recall_at_10": 0.98,
        "source": f"npm run eval -- --record-from {run.name}",
    }
    shown = model_catalog.scores("openai:gpt-6-luna")
    assert shown is not None
    assert shown["judge_name"] == "Claude Opus 5.5"


@pytest.mark.parametrize(
    ("made", "said"),
    [
        ({"judged": False}, "wasn't judged"),
        ({"question_set": "another"}, "calibration half"),
        ({"finish": False}, "isn't a finished run"),
    ],
)
def test_a_run_that_cant_stand_for_its_model_isnt_recorded(
    tmp_path, measured_file, made, said
):
    run = kept_run(tmp_path, SOL, [answer("q1", True)], **made)
    if made.get("finish", True):
        (run / "report.json").write_text(
            json.dumps({**REPORT, "answer_model": SOL, "answered_by": [SOL]})
        )
    before = measured_file.read_text()
    with pytest.raises(standing.CantStand, match=said):
        measured.record_from(cli.Kept(run))
    assert measured_file.read_text() == before


def test_a_run_a_fallback_answered_isnt_recorded(tmp_path, measured_file):
    run = finished(tmp_path, SOL, "gemma4:26b")  # Offline's local model answered
    with pytest.raises(standing.CantStand, match="didn't write every"):
        measured.record_from(cli.Kept(run))


def test_a_local_run_goes_to_the_library_named_tied_to_its_build(
    tmp_path, measured_file, library
):
    run = finished(tmp_path, LOCAL, "gemma4:26b", local_version=BUILD)
    with pytest.raises(measured.NotRecorded, match="--record-local-to"):
        measured.record_from(cli.Kept(run))
    before = measured_file.read_text()
    assert cli.main(["--record-from", str(run), "--record-local-to", str(library)]) == 0
    assert measured_file.read_text() == before, "the app's list is the cloud's"
    saved = json.loads((library / "models" / "measured.json").read_text())
    kept = saved["models"]["gemma4:26b"]
    assert kept["digest"] == BUILD
    assert kept["judged"] is True
    assert kept["answers_correct"] == 0.9556
    assert kept["kept"] == run.name


def test_an_unjudged_local_run_says_so(tmp_path, library):
    run = finished(tmp_path, LOCAL, "gemma4:26b", judged=False, local_version=BUILD)
    measured.record_local_to(cli.Kept(run), library)
    kept = json.loads((library / "models" / "measured.json").read_text())
    scores = kept["models"]["gemma4:26b"]
    assert scores["judged"] is False
    assert scores["judge_model"] is None
    assert "answers_correct" not in scores
    assert scores["facts_found"] == 0.98


def test_the_library_is_named_and_outside_the_repo(tmp_path, library):
    run = finished(tmp_path, LOCAL, "gemma4:26b", local_version=BUILD)
    with pytest.raises(SystemExit):
        cli.main(["--record-local-to", str(library)])  # which run?
    with pytest.raises(measured.NotRecorded, match="inside the repo"):
        measured.record_local_to(cli.Kept(run), measured.REPO / "backend")
    with pytest.raises(measured.NotRecorded, match="no library.db"):
        measured.record_local_to(cli.Kept(run), tmp_path / "Nothing")
    assert measured.vault_checked(library) == library.resolve()


def test_no_library_inside_the_repo_is_written_the_development_vault_included(
    tmp_path, monkeypatch
):
    """A vault inside the repo is refused, with no exception."""
    repo = tmp_path / "repo"
    (repo / ".vault-dev").mkdir(parents=True)
    (repo / ".vault-dev" / "library.db").write_bytes(b"")
    monkeypatch.setattr(measured, "REPO", repo)
    for inside in (repo / ".vault-dev", repo):
        with pytest.raises(measured.NotRecorded, match="inside the repo"):
            measured.vault_checked(inside)


def test_a_cloud_run_isnt_written_to_a_library(tmp_path, library):
    run = finished(tmp_path, SOL, SOL)
    with pytest.raises(measured.NotRecorded, match="ship with the app"):
        measured.record_local_to(cli.Kept(run), library)
    assert not (library / "models").exists()


def test_a_local_run_with_no_build_isnt_written(tmp_path, library):
    run = finished(tmp_path, LOCAL, "gemma4:26b", local_version="not listed")
    with pytest.raises(measured.NotRecorded, match="names no build"):
        measured.record_local_to(cli.Kept(run), library)
