"""A run's expected cost: learned from the last kept run of the same model and set,
each part from its own call records, or each call at its most for the calls
assumed, and said so."""

from pathlib import Path

import pytest

from agent import harness
from backend import agent_service
from evals import cli, estimate, run, score
from evals import kept as keeping
from evals.tests.fake_runs import fake_evaluate
from evals.tests.kept_runs import own
from evals.tests.questions import QUESTION, SOL

PRIVATE = {**QUESTION, "id": "q2", "private": True}
QUESTIONS = [QUESTION, PRIVATE]
HELPER = "anthropic:claude-haiku-5-5"
LOCAL = "local:gemma4:26b"


def call(purpose: str, cost: float) -> dict:
    return {"purpose": purpose, "cost_usd": cost}


ROWS = [
    {
        "id": "q1",
        "calls": [
            call("rewrite", 0.001),
            call("answer", 0.02),
            call("answer", 0.03),  # the grade's retry
            call("eval_judge", 0.004),
            call("eval_judge", 0.005),
        ],
    },
    {"id": "q2", "calls": [call("answer", 0.0)]},  # Private, on this Mac
]


def keep(root, rows=ROWS, end=True, **made):
    """A kept run of SOL on QUESTIONS, judged, unless `made` says otherwise."""
    model = made.get("model", SOL)
    manifest = {
        "answer_model": model,
        "question_set": score.set_hash(made.get("questions", QUESTIONS)),
        "asked": score.asked_hash(made.get("questions", QUESTIONS)),
        "judged": made.get("judged", True),
    }
    kept = keeping.start(root, model, manifest)
    for row in rows:
        kept.row(row)
    if end:
        kept.finish(own(kept), [row["id"] for row in rows])
    return kept


def test_each_part_is_learned_from_the_last_kept_run(tmp_path):
    keep(tmp_path, rows=[{**row, "calls": []} for row in ROWS])  # an older run
    newest = keep(tmp_path)
    found = estimate.expected(QUESTIONS, SOL, HELPER, True, tmp_path)
    assert found.parts == {
        estimate.ANSWERS: 0.02,
        estimate.HELPERS: 0.001,
        estimate.RETRIES: 0.03,
        estimate.JUDGE: 0.009,
    }
    assert found.usd == 0.06
    assert found.learned_from == newest.folder.name
    assert found.assumed == ()
    assert newest.folder.name in found.line()
    assert "at its most" not in found.line()


def test_a_run_of_another_model_or_set_or_unfinished_isnt_learned_from(tmp_path):
    keep(tmp_path, model="openai:gpt-6-luna")
    keep(tmp_path, questions=[QUESTION])
    keep(tmp_path, end=False)
    stopped = keep(tmp_path, end=False)
    stopped.stop_at_limit(own(stopped))
    found = estimate.expected(QUESTIONS, SOL, HELPER, True, tmp_path)
    assert found.learned_from is None
    assert set(found.assumed) == set(estimate.PARTS)


def test_a_refused_answer_call_is_neither_an_answer_nor_its_retry():
    """A call refused before sending is recorded,
    costs nothing, and leaves the answer that went as the answer, not a retry."""
    refused = {**call("answer", 0.0), "outcome": "refused"}
    found = estimate.from_rows([{"id": "q1", "calls": [refused, call("answer", 0.02)]}])
    assert found[estimate.ANSWERS] == 0.02
    assert found[estimate.RETRIES] == 0.0


def test_a_questions_cost_is_every_call_it_sent():
    """What a failed question's row says it cost: its calls', a refused one aside."""
    refused = {**call("answer", 0.5), "outcome": "refused"}
    cut = {**call("answer", 0.01), "outcome": "cut"}
    calls = [call("plan", 0.002), cut, refused, {"purpose": "answer"}]
    assert estimate.cost_of(calls) == pytest.approx(0.012)
    assert estimate.cost_of([]) == 0.0


def test_a_failed_question_costs_what_its_calls_cost(lease, tmp_path, monkeypatch):
    """GPT-6.1 Sol's answer is billed, then the question's run fails: its row and the
    run's total say what its calls cost, not $0.00."""

    def fails_after_the_answer(*_args):
        raise RuntimeError("the run fails after its answer call")

    monkeypatch.setattr(harness, "_reply_update", fails_after_the_answer)
    report, _claims = run.evaluate([QUESTION], SOL, tmp_path / "runs", limit_usd=50.0)
    (row,) = keeping.Kept(Path(report["kept"])).rows()
    assert row["state"] == score.FAILED
    billed = [c for c in row["calls"] if c["purpose"] == "answer"]
    assert billed, "the answer call was made"
    assert all(c["cost_usd"] > 0 for c in billed), "and billed"
    every = sum(c["cost_usd"] for c in row["calls"] if c["outcome"] != "refused")
    assert row["cost_usd"] == pytest.approx(every)
    assert row["cost_usd"] >= sum(c["cost_usd"] for c in billed)
    assert report["cost_usd"] == pytest.approx(round(every, 4))


def test_with_no_kept_run_each_call_is_at_its_most(tmp_path):
    found = estimate.expected(QUESTIONS, SOL, HELPER, True, tmp_path)
    # One Standard question: the Private one is answered on this Mac, never judged.
    assert found.parts == estimate.at_most(1, SOL, HELPER)
    for part, cost in estimate.at_most(2, SOL, HELPER).items():
        assert found.parts[part] == pytest.approx(cost / 2, abs=1e-4)
    assert all(cost > 0 for cost in found.parts.values())
    line = found.line()
    assert "each call there is priced at its most" in line
    assert "A run can make more judge calls than that" in line, "not called a ceiling"
    assert "ceiling" not in line
    # The judge reads a passage per call, and is assumed to make several.
    assert found.parts[estimate.JUDGE] > found.parts[estimate.ANSWERS]


def test_the_answer_at_its_most_prices_the_whole_standard_budget():
    budget = estimate.budgets.STANDARD
    sent = (
        budget.memories
        + budget.summary
        + budget.turns
        + budget.evidence
        + estimate._rules_tokens()  # noqa: SLF001
    )
    price = estimate.model_catalog.price(SOL)
    assert price is not None
    expected = (sent * price[0] + budget.output * price[1]) / 1_000_000
    assert estimate.at_most(1, SOL, HELPER)[estimate.ANSWERS] == round(expected, 4)


def test_an_unjudged_run_is_estimated_with_no_judge(tmp_path):
    keep(tmp_path)
    found = estimate.expected(QUESTIONS, SOL, HELPER, False, tmp_path)
    assert found.parts[estimate.JUDGE] == 0.0
    assert found.parts[estimate.ANSWERS] == 0.02
    unkept = estimate.expected(QUESTIONS, SOL, HELPER, False, tmp_path / "none")
    assert unkept.parts[estimate.JUDGE] == 0.0
    assert estimate.JUDGE not in unkept.assumed


def test_a_cloud_run_lists_the_model_on_this_mac_its_private_questions_ask(
    library, tmp_path
):
    """A Private question is asked by
    the local model, so the estimate names it beside the cloud's, as the vault
    sets it."""
    local = f"local:{agent_service.local_tag()} for the Private questions, on this Mac"
    found = run.expected_cost(QUESTIONS, SOL, True, tmp_path)
    assert found.calls[-1] == local
    assert local in found.line()
    standard = run.expected_cost([QUESTION], SOL, True, tmp_path)
    assert local not in standard.calls
    assert len(standard.calls) == 3, "to answer, to help and to judge"
    free = estimate.expected(QUESTIONS, LOCAL, "local", False, tmp_path)
    assert estimate.asked_here(free, QUESTIONS, LOCAL, "gemma4:26b") == free, (
        "a local run asks every question here, as its answer model"
    )


def test_a_judged_run_after_an_unjudged_one_has_its_judge_assumed(tmp_path):
    keep(tmp_path, judged=False, rows=[{**ROWS[0], "calls": ROWS[0]["calls"][:3]}])
    found = estimate.expected(QUESTIONS, SOL, HELPER, True, tmp_path)
    assert found.parts[estimate.ANSWERS] == 0.02
    assert found.parts[estimate.JUDGE] == estimate.at_most(1, SOL, HELPER)["judge"]
    assert found.assumed == (estimate.JUDGE,)


def test_a_judged_kept_run_is_learned_from_before_a_newer_unjudged_one(tmp_path):
    judged = keep(tmp_path)
    keep(tmp_path, judged=False)
    found = estimate.expected(QUESTIONS, SOL, HELPER, True, tmp_path)
    assert found.learned_from == judged.folder.name


def test_a_local_unjudged_run_costs_nothing(tmp_path):
    found = estimate.expected(QUESTIONS, LOCAL, "local", False, tmp_path)
    assert found.usd == 0.0
    assert found.free
    assert found.line().startswith("Expected cost: $0.00.")
    judged = estimate.expected(QUESTIONS, LOCAL, "local", True, tmp_path)
    assert judged.parts[estimate.ANSWERS] == 0.0
    assert judged.parts[estimate.JUDGE] > 0, "the judge is in the cloud"


@pytest.fixture
def cli_run(tmp_path, monkeypatch, library):
    monkeypatch.setattr(cli, "REPORTS", tmp_path)
    return tmp_path


def test_estimate_only_prints_it_and_stops(cli_run, monkeypatch, capsys):
    def never(*_given, **_named):
        raise AssertionError("the run started")

    monkeypatch.setattr(cli.run, "evaluate", never)
    assert cli.main(["--model", SOL, "--estimate-only"]) == 0
    assert capsys.readouterr().out.startswith("Expected cost: $")
    assert not (cli_run / cli.RUNS).exists()


def test_every_run_prints_its_estimate_before_it_starts(cli_run, monkeypatch, capsys):
    printed: list[str] = []
    started = fake_evaluate({"questions": 1})

    def evaluate(*given, **named):
        printed.append(capsys.readouterr().out)
        return started(*given, **named)

    monkeypatch.setattr(cli.run, "evaluate", evaluate)
    cli.main(["--model", SOL, "--skip-hard-pages", "--limit-usd", "50"])
    assert printed[0].startswith("Expected cost: $")


def test_the_model_cache_is_named_before_the_estimate_counts_tokens(
    cli_run, monkeypatch
):
    # A judged estimate once loaded the tokenizer first, so Hugging Face kept its
    # own home, and the run's embedder then failed to load from there.
    order: list[str] = []
    monkeypatch.setattr(cli.vault, "use_model_cache", lambda: order.append("cache"))
    counted = estimate.tokens.count
    monkeypatch.setattr(
        estimate.tokens, "count", lambda text: order.append("tokens") or counted(text)
    )
    assert cli.main(["--model", SOL, "--estimate-only"]) == 0
    assert order[0] == "cache", order[:3]
    assert "tokens" in order, "the judged estimate counts its instructions"


def test_no_run_or_estimate_without_a_vault_named(monkeypatch, capsys):
    """With none named, the vault would be the owner's own library."""
    monkeypatch.delenv(cli.vault.VAULT_ENV)
    monkeypatch.delenv(cli.vault.LEGACY_VAULT_ENV, raising=False)

    def never(*_given, **_named):
        raise AssertionError("the vault was opened")

    monkeypatch.setattr(cli.run, "expected_cost", never)
    monkeypatch.setattr(cli.run, "evaluate", never)
    with pytest.raises(SystemExit):
        cli.main(["--estimate-only"])
    assert cli.vault.VAULT_ENV in capsys.readouterr().err
