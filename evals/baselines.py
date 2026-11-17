"""Each measured model's own baseline, `evals/baselines/<model>.json`, written from a
finished, judged kept run on the calibration half: its report without the rows, with
Private questions in their own section, as every report has them. A model is only
ever compared with its own. A baseline names its judge: one judged by any other than
Claude Opus 5.5 is kept beside Opus's, `<model>.judged-by-<judge>.json`, never in its
place. `evals/baseline.json` stays as it is."""

import json
from pathlib import Path

from agent.models import JUDGE_MODEL
from evals import compare, kept, standing

FOLDER = Path(__file__).with_name("baselines")


def path(model: str, judge: str = JUDGE_MODEL) -> Path:
    name = kept.UNSAFE.sub("_", model)
    if judge != JUDGE_MODEL:
        name += f".judged-by-{kept.UNSAFE.sub('_', judge)}"
    return FOLDER / f"{name}.json"


def write(kept_run: kept.Kept) -> Path:
    """The run's report, without its rows, as its answering model's baseline."""
    report = standing.checked(kept_run)
    baseline = {k: v for k, v in report.items() if k != "rows"}
    baseline["kept"] = kept_run.folder.name
    judge = compare.judge_of(kept_run)
    baseline["judge_model"] = judge
    target = path(str(report["answer_model"]), judge)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(baseline, indent=1) + "\n")
    return target
