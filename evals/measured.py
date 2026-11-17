"""Scores where you choose a model: a finished
kept run's scores, written where Settings reads them, asking no model. A cloud model's
go in `backend/model_measured.json`, which ships with the app (`--record-from <run>`).
A local model's go in the library named on the command line, `<vault>/models/
measured.json`, keyed by its tag and the build Ollama reported for it, since a tag can
be rebuilt (`--record-from <run> --record-local-to <vault>`). A vault taken from the
environment could be the owner's real one, so it's never assumed."""

import json
from pathlib import Path

from backend import local_measured, model_catalog
from evals import compare, run, score, standing
from evals import kept as keeping

REPO = Path(__file__).parents[1]


class NotRecorded(SystemExit):
    """These scores can't be recorded where they were asked to go."""


def _day(kept: keeping.Kept) -> str:
    return str(kept.manifest.get("ended_at") or "")[:10]


def record_from(kept: keeping.Kept) -> str:
    """A cloud model's scores, from a finished, judged kept run on the calibration
    half whose Standard answers it wrote all of, refused as `--baseline-from`
    refuses one. Returns the model recorded."""
    report = standing.checked(kept)
    model = str(report["answer_model"])
    if run.local_of(model) is not None:
        raise NotRecorded(
            f"{kept.folder.name} is a local model's run: its scores go in your "
            "library, with --record-local-to <vault>."
        )
    measured = model_catalog.MEASURED  # read when it's written, as tests move it
    found = json.loads(measured.read_text())
    found["models"][model] = {
        **score.measured_entry(report, _day(kept), compare.judge_of(kept)),
        "source": f"npm run eval -- --record-from {kept.folder.name}",
    }
    measured.write_text(json.dumps(found, indent=2) + "\n")
    return model


def vault_checked(vault: Path) -> Path:
    """The vault named on the command line: an existing library, outside the repo."""
    folder = vault.resolve()
    if folder.is_relative_to(REPO.resolve()):
        raise NotRecorded(f"{vault} is inside the repo; name your library's folder.")
    if not (folder / "library.db").exists():
        raise NotRecorded(f"{vault} isn't a SecureFolio library: it has no library.db.")
    return folder


def record_local_to(kept: keeping.Kept, vault: Path) -> Path:
    """A local model's scores, from a finished kept run on the calibration half
    whose Standard answers it wrote all of, judged or not, into the vault named:
    under its tag, with the build that was measured. Returns the file written."""
    folder = vault_checked(vault)
    report = standing.checked(kept, judged=False)
    tag = run.local_of(str(report["answer_model"]))
    build = kept.manifest.get("local_version")
    if tag is None:
        raise NotRecorded(
            f"{kept.folder.name} is a cloud model's run: its scores ship with the "
            "app, with --record-from alone."
        )
    if not build or build == "not listed":
        raise NotRecorded(f"{kept.folder.name} names no build of {tag}.")
    judge = compare.judge_of(kept) if kept.manifest.get("judged") else None
    target = local_measured.path_in(folder)
    found = json.loads(target.read_text()) if target.exists() else {"models": {}}
    found["models"][tag] = {
        "digest": build,
        **score.measured_entry(report, _day(kept), judge),
        "kept": kept.folder.name,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(found, indent=2) + "\n")
    return target
