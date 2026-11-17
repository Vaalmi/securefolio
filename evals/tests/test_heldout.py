"""The held-out command refuses until the owner has sealed, and prints aggregates."""

import json
from pathlib import Path

import pytest

from data.build import library
from evals import heldout, run
from evals.estimate import Estimate


def test_it_says_so_when_nothing_is_sealed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(heldout, "ANSWERS", tmp_path)
    assert heldout.main(["--limit-usd", "50"]) == 2
    assert "owner" in capsys.readouterr().out


def test_it_prints_aggregates_and_fails_on_a_miss(tmp_path, monkeypatch, capsys):
    (tmp_path / "seed.json").write_text("{}")
    monkeypatch.setattr(heldout, "ANSWERS", tmp_path)
    monkeypatch.setattr(heldout, "report", lambda *_given: {"recall_at_10": 0.5})
    assert heldout.main(["--limit-usd", "50"]) == 1
    out = capsys.readouterr().out
    assert "recall_at_10" in out
    assert "question" not in out


def test_a_sealed_library_can_be_seeded(tmp_path):
    questions = tmp_path / "questions.json"
    library.build_held_out(tmp_path / "library", 7, questions)
    assert (tmp_path / "library" / "folders.json").exists()
    assert (tmp_path / "locators.json").exists()
    assert json.loads(questions.read_text())


def test_a_report_with_no_hard_pages_fails(tmp_path, monkeypatch, capsys):
    (tmp_path / "seed.json").write_text("{}")
    monkeypatch.setattr(heldout, "ANSWERS", tmp_path)
    good = {
        "recall_at_10": 1.0,
        "citation_precision": 1.0,
        "unanswerable": 1.0,
    }
    monkeypatch.setattr(heldout, "report", lambda *_given: good)
    assert heldout.main(["--limit-usd", "50"]) == 1
    assert "table_cells wasn't measured" in capsys.readouterr().out


@pytest.mark.parametrize("script", ["eval", "evals:heldout"])
def test_the_commands_bring_the_models_and_the_key(script):
    """A temporary vault starts with no models, and neither shell has the key."""
    package = Path(__file__).parents[2] / "package.json"
    command = json.loads(package.read_text())["scripts"][script]
    assert "SECUREFOLIO_MODEL_CACHE=" in command
    assert "--env-file .env" in command


def test_the_hard_page_script_can_find_the_repo(tmp_path, monkeypatch):
    """It runs as a script, whose path holds its own folder, wherever it's started."""
    script = tmp_path / "script.py"
    script.write_text("import json\nimport backend\nprint(json.dumps({'found': 1}))\n")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setattr(run, "HARD_PAGES", Path(__file__).parents[2] / "scripts/x/s.py")
    real = run.subprocess.run

    def from_elsewhere(command, **options):
        return real([command[0], str(script)], **options)

    monkeypatch.setattr(run.subprocess, "run", from_elsewhere)
    assert run._hard_measure("routing", "part", tmp_path) == {"found": 1}


@pytest.fixture
def sealed(tmp_path, monkeypatch):
    """A sealed set the command finds, seeded as nothing: the run is stood in for."""
    from data.build import seed  # noqa: PLC0415  as heldout.report imports it

    (tmp_path / "seed.json").write_text("{}")
    (tmp_path / "questions.json").write_text("[]")
    monkeypatch.setattr(heldout, "ANSWERS", tmp_path)
    monkeypatch.setattr(seed, "seed_from", lambda _library: None)
    expected = Estimate({"answers": 1.0, "helpers": 0.0, "retries": 0.0, "judge": 0.5})
    monkeypatch.setattr(run, "expected_cost", lambda *_given: expected)
    monkeypatch.setattr(run, "hard_pages", lambda *_given: {})
    return tmp_path


def test_the_held_out_run_prints_its_estimate_before_it_starts(
    sealed, monkeypatch, capsys
):
    printed: list[str] = []

    def evaluate(*_given, **named):
        printed.append(capsys.readouterr().out)
        return {"limit_usd": named["limit_usd"]}, []

    monkeypatch.setattr(run, "evaluate", evaluate)
    heldout.main(["--limit-usd", "2"])
    assert printed[0].startswith("Expected cost: $1.50")


def test_held_out_estimate_only_prints_it_and_stops(sealed, monkeypatch, capsys):
    def never(*_given, **_named):
        raise AssertionError("the run started")

    monkeypatch.setattr(run, "evaluate", never)
    assert heldout.main(["--estimate-only"]) == 0
    assert capsys.readouterr().out.startswith("Expected cost: $1.50")


def test_the_held_out_run_needs_a_limit(sealed, monkeypatch):
    def never(*_given, **_named):
        raise AssertionError("the run started")

    monkeypatch.setattr(run, "evaluate", never)
    for given in ([], ["--limit-usd", "0"], ["--limit-usd", "nan"]):
        with pytest.raises(run.NoLimit):
            heldout.main(given)


def test_the_held_out_run_prints_steps_as_counts_never_as_questions(
    sealed, monkeypatch, capsys
):
    found = {
        "limit_usd": 2.0,
        "wrong_by_step": {"facts": 1},
        "wrong_questions_by_step": {"facts": ["sealed-q7"]},
        "private_wrong_questions_by_step": {"facts": ["sealed-q9"]},
        "rows": [{"id": "sealed-q7"}],
    }
    monkeypatch.setattr(run, "evaluate", lambda *_given, **_named: (found, []))
    heldout.main(["--limit-usd", "2"])
    out = capsys.readouterr().out
    assert '"facts": 1' in out
    assert "sealed-q7" not in out
    assert "sealed-q9" not in out
