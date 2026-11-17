"""Made-up kept runs for the tests that read kept runs: side by side and baselines."""

from pathlib import Path

from evals import kept as keeping
from evals import score

SOL = "openai:gpt-6.1-sol"
LOCAL = "local:gemma4:26b"
SET = "set-a"


def answer(question: str, right: bool, **changed) -> dict:
    return {
        "id": question,
        "category": "fact",
        "private": False,
        "state": score.ANSWERED,
        "rank": 1 if right else None,
        "facts_found": right,
        "correct": right,
        "kind": "answer",
        "parts": 1,
        "uncited": 0,
        "cost_usd": 0.0,
        "thrown_away": False,
        "claims": [{"claim": "It is.", "supported": right}],
        **changed,
    }


def own(kept: keeping.Kept, **report) -> dict:
    """A report with the run's own figures section, as every ended run's report has."""
    return {**report, "figures": {"run": kept.folder.name}}


def kept_run(
    root: Path, model: str, rows: list[dict], finish: bool = True, **manifest
) -> Path:
    """A kept run of these rows; `manifest` changes judged, question_set or scoring."""
    manifest = {
        "answer_model": model,
        "judged": True,
        "question_set": SET,
        "scoring": score.SCORING_VERSION,
        **manifest,
    }
    kept = keeping.start(root, model, manifest)
    for row in rows:
        kept.row(row if manifest["judged"] else {**row, "correct": None, "claims": []})
    if finish:
        kept.finish(own(kept), [row["id"] for row in rows])
    return kept.folder
