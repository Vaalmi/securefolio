"""Whether a kept run can stand for its model, as its baseline (`--baseline-from`) or
its scores in Settings (`--record-from`): finished, judged where that's asked, on the
calibration half, and every Standard answer the model's own."""

from typing import Any

from evals import compare, kept, run


class CantStand(SystemExit):
    """This kept run can't stand for its model."""


def checked(kept_run: kept.Kept, judged: bool = True) -> dict[str, Any]:
    """The run's report, if it can stand for its model: finished, judged when
    `judged` asks, on the calibration half, and every Standard answer its own."""
    manifest = kept_run.manifest
    report = kept_run.report()
    name = kept_run.folder.name
    if manifest.get("state") != kept.FINISHED or report is None:
        raise CantStand(f"{name} isn't a finished run.")
    if judged and not manifest.get("judged"):
        raise CantStand(f"{name} wasn't judged: a baseline is.")
    if manifest.get("question_set") != compare.calibration_set():
        raise CantStand(f"{name} isn't on the calibration half.")
    model = str(report.get("answer_model"))
    if report.get("answered_by") != [run.local_of(model) or model]:
        # A fallback's answers, the local model's under Offline say, aren't the
        # model's own. Private questions are answered on this Mac, and kept apart.
        writers = ", ".join(report.get("answered_by") or ["nothing"])
        raise CantStand(
            f"{model} didn't write every Standard answer in {name} "
            f"(written by {writers})."
        )
    return report
