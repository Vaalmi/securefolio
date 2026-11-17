"""A run's expected cost, shown before it starts. It learns from the last finished
run of the same model on the same questions: its answers, helpers, retries and judge
calls, each from its rows' call records. With no such run, each call is priced at its
most, from `agent/budgets.py` and the catalog's price, for a number of calls assumed per
question, and the estimate says so: that is no ceiling on the run, as an answer can
make more judge calls than assumed. It is a figure for the owner only: what may be
sent is decided by the run's limit, in `agent/models.py`, never by this."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agent import budgets, harness, models, prompts
from backend import agent_service, model_catalog, settings, tokens
from evals import kept as keeping
from evals import score

ANSWERS = "answers"
HELPERS = "helpers"
RETRIES = "retries"
JUDGE = "judge"
PARTS = (ANSWERS, HELPERS, RETRIES, JUDGE)
ANSWER_CALL = "answer"  # a row's first answer call; any after it is the grade's retry
JUDGE_CALL = "eval_judge"
# With no kept run, each Standard question is assumed to make one planning call, one
# answer, the grade's one retry, and a judge call for every passage it was handed. The
# judge's count is an assumption, not a bound: it judges each citation of each
# sentence, and each uncited sentence, so a long answer citing every passage in every
# sentence makes many more. Only the run's limit bounds what it spends.
JUDGE_CALLS = harness.KEEP
LOCAL_PREFIX = "local:"


@dataclass(frozen=True)
class Estimate:
    parts: dict[str, float]
    assumed: tuple[str, ...] = ()  # the parts no kept run showed, each call at its most
    learned_from: str | None = None  # the kept run's folder
    free: bool = False  # nothing in the run can go to the cloud
    calls: tuple[str, ...] = ()  # each model the run calls, and what for

    @property
    def usd(self) -> float:
        return round(sum(self.parts.values()), 4)

    def line(self) -> str:
        calls = f" It calls {', '.join(self.calls)}." if self.calls else ""
        if self.free:
            return f"Expected cost: $0.00. Nothing in this run leaves the Mac.{calls}"
        each = ", ".join(f"{part} ${cost:.2f}" for part, cost in self.parts.items())
        said = f"Expected cost: ${self.usd:.2f} ({each})"
        if self.learned_from:
            said += f", from the run kept as {self.learned_from}"
        if self.assumed:
            said += (
                f". No kept run of this model on these questions shows "
                f"{', '.join(self.assumed)}: each call there is priced at its most, "
                f"for one planning call, one answer, one retry and {JUDGE_CALLS} judge "
                "calls a question. A run can make more judge calls than that; its "
                "limit is what bounds it"
            )
        return said + "." + calls


def _is_local(model: str) -> bool:
    return model == settings.LOCAL or model.startswith(LOCAL_PREFIX)


def _price(model: str) -> tuple[float, float]:
    """A local model costs nothing. A cloud one with no known price is refused before
    the run starts, so it's never estimated."""
    if _is_local(model):
        return (0.0, 0.0)
    return model_catalog.price(model) or (0.0, 0.0)


def _at(price: tuple[float, float], sent: int, written: int) -> float:
    return (sent * price[0] + written * price[1]) / 1_000_000


def _rules_tokens() -> int:
    """The longest of the instructions, as any call may carry it."""
    return max(
        tokens.count(prompts.load(path.stem)) for path in prompts.FOLDER.glob("*.md")
    )


def at_most(
    standard: int, answering: str, helper: str, turns: int | None = None
) -> dict[str, float]:
    """Each call at its most, for the calls assumed of `standard` Standard questions:
    a Private question is answered on this Mac and never judged. `turns` counts
    each question's earlier questions too, which are answered but not judged."""
    turns = standard if turns is None else turns
    budget = budgets.STANDARD
    rules = _rules_tokens()
    history = budget.memories + budget.summary + budget.turns
    answer = _at(_price(answering), history + budget.evidence + rules, budget.output)
    planning = _at(_price(helper), history + rules, models.HELPER_MAX_TOKENS)
    judging = _at(
        _price(agent_service.judge_model()),
        budget.evidence + rules,
        models.JUDGE_MAX_TOKENS,
    )
    return {
        ANSWERS: round(turns * answer, 4),
        HELPERS: round(turns * planning, 4),
        RETRIES: round(turns * answer, 4),
        JUDGE: round(standard * JUDGE_CALLS * judging, 4),
    }


def sent(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The calls that went: a refused one was never sent, so it cost nothing and
    answered nothing."""
    return [call for call in calls if call.get("outcome") != "refused"]


def cost_of(calls: list[dict[str, Any]]) -> float:
    """What a question's calls cost, every attempt included, as its call records
    and the run's limit count them."""
    return sum(float(call.get("cost_usd") or 0.0) for call in sent(calls))


def from_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    """What each part of a kept run cost, from its call records."""
    found = dict.fromkeys(PARTS, 0.0)
    for row in rows:
        answers = 0
        for call in sent(row.get("calls") or []):
            cost = float(call.get("cost_usd") or 0.0)
            if call["purpose"] == ANSWER_CALL:
                found[RETRIES if answers else ANSWERS] += cost
                answers += 1
            elif call["purpose"] == JUDGE_CALL:
                found[JUDGE] += cost
            else:
                found[HELPERS] += cost
    return {part: round(cost, 4) for part, cost in found.items()}


def last_kept(
    root: Path, answering: str, asked: str, judge: bool
) -> keeping.Kept | None:
    """The newest finished run of this model that asked these very questions, its
    follow-ups included, a judged one first when this run is judged."""
    same = [
        kept
        for kept in keeping.finished_runs(root)
        if kept.manifest.get("answer_model") == answering
        and kept.manifest.get("asked") == asked
        and not kept.manifest.get("rejudged_from")  # its calls are the judge's alone
    ]
    judged = [kept for kept in same if kept.manifest.get("judged")]
    chosen = judged if judge and judged else same
    return chosen[-1] if chosen else None


def free(answering: str, helper: str, judge: bool) -> bool:
    """Whether nothing in the run can go to the cloud: it's unjudged, and both the
    answer and the helpers are local."""
    return not judge and _is_local(answering) and _is_local(helper)


def expected(
    questions: list[dict[str, Any]],
    answering: str,
    helper: str,
    judge: bool,
    root: Path,
) -> Estimate:
    calls = (
        f"{answering} to answer",
        f"{helper} to help",
        *((f"{agent_service.judge_model()} to judge",) if judge else ()),
    )
    if free(answering, helper, judge):
        return Estimate(dict.fromkeys(PARTS, 0.0), free=True, calls=calls)
    asked = [question for question in questions if not question["private"]]
    turns = sum(1 + len(question.get("earlier") or []) for question in asked)
    most = at_most(len(asked), answering, helper, turns)
    kept = last_kept(root, answering, score.asked_hash(questions), judge)
    if kept is None:
        parts = {**most, JUDGE: most[JUDGE] if judge else 0.0}
        return Estimate(parts, tuple(p for p in PARTS if parts[p]), calls=calls)
    parts = from_rows(kept.rows())
    assumed: tuple[str, ...] = ()
    if not judge:
        parts[JUDGE] = 0.0
    elif not kept.manifest.get("judged") or (
        kept.manifest.get("judge_model", models.JUDGE_MODEL)
        != agent_service.judge_model()
    ):
        # Another judge's calls price nothing of this one's.
        parts[JUDGE] = most[JUDGE]
        assumed = (JUDGE,)
    return Estimate(parts, assumed, kept.folder.name, calls=calls)


def asked_here(
    found: Estimate, questions: list[dict[str, Any]], answering: str, local: str
) -> Estimate:
    """With the model on this Mac named, tag `local`, when a cloud run has Private
    questions: they're asked here, whatever answers the rest."""
    if _is_local(answering) or not any(q["private"] for q in questions):
        return found
    here = f"{LOCAL_PREFIX}{local} for the Private questions, on this Mac"
    return replace(found, calls=(*found.calls, here))
