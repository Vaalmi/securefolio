"""A run's spending limit: a request whose ceiling would take the run past it is never
sent, the run stops there and is kept as stopped at the limit, and the question that
met it is stopped, not wrong. No run that may spend goes without a limit."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa
from langchain_core.messages import AIMessageChunk

from agent import api, models
from backend import database, egress, sources, spend, states
from evals import cli, run, score
from evals import kept as keeping
from evals.tests.questions import DATA, PLANNED, QUESTION, SOL
from evals.tests.stub_providers import answering_with, judged
from testing.queue import drain
from testing.stub_models import CUT_SHORT, completion

MILLION = 1_000_000
# Each answer has two sentences, each citing its source: two judge calls a question.
TWO_CITED = answering_with("The pet deposit is $299.11. [1] It is paid at signing. [1]")
# Each question is planned, so its calls are a plan, an answer and the judge's.
FIRST = PLANNED
SECOND = {**PLANNED, "id": "q2"}


def billed_openai(path: str, body: dict) -> dict:
    """GPT-6.1 Sol, $2 a million in: each call bills a million, so $2."""
    reply = TWO_CITED(path, body)
    usage = {"prompt_tokens": MILLION, "completion_tokens": 0, "total_tokens": MILLION}
    return {**reply, "usage": usage}


def billed_judge(path: str, body: dict) -> dict:
    """Claude Opus 5.5, $4 a million in: each judge call bills a million, so $4."""
    return {
        **judged(path, body),
        "usage": {"input_tokens": MILLION, "output_tokens": 0},
    }


@pytest.fixture
def lease(stubs, tmp_path, monkeypatch):
    home = tmp_path / "Home"
    shutil.copytree(DATA / "Home", home)
    sources.add_source(str(home), states.STANDARD)
    drain()
    monkeypatch.setattr(cli, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(cli, "load_questions", lambda _path: [FIRST, SECOND])
    anthropic, openai = stubs
    anthropic.requests.clear()
    openai.requests.clear()
    return stubs


def _sol_helper(monkeypatch):
    """Sol plans too, so each question's calls are: plan $2, answer $2, judge $4 and
    $4, $12 in all."""
    stored = run.settings.get

    def get(key):
        return SOL if key == run.settings.MODEL_HELPER else stored(key)

    monkeypatch.setattr(run.settings, "get", get)


@pytest.mark.parametrize(
    ("limit", "sent"),
    [
        # After q1's $12, q2's plan would pass $12.01: refused during a helper call.
        (12.01, (2, 2)),
        # q2's plan brings it to $14, so its answer would pass $14.01.
        (14.01, (3, 2)),
        # q2's plan, answer and first judge call bring it to $20, so its second judge
        # call would pass $20.01.
        (20.01, (4, 3)),
    ],
    ids=["during a helper call", "during an answer", "between two judge calls"],
)
def test_the_run_stops_before_the_request_that_would_pass_its_limit(
    lease, monkeypatch, capsys, limit, sent
):
    """`sent`: the requests that went to Sol and to the judge. These stubs bill far
    more than their requests' bytes, so the limit lands on a known call."""
    anthropic, openai = lease
    anthropic.reply, openai.reply = billed_judge, billed_openai
    _sol_helper(monkeypatch)
    began = spend.spent_this_month()
    assert cli.main(["--model", SOL, "--limit-usd", str(limit)]) == cli.FAILED
    assert (len(openai.requests), len(anthropic.requests)) == sent, (
        "the request over the limit wasn't sent"
    )
    assert spend.spent_this_month() - began <= limit
    (folder,) = (cli.REPORTS / cli.RUNS).iterdir()
    kept = keeping.Kept(folder)
    assert kept.state == keeping.STOPPED_AT_LIMIT
    first, second = kept.rows()
    assert first["id"] == "q1"
    assert first["state"] == score.ANSWERED, "the earlier row is kept as it was"
    assert first["correct"] is not None
    assert second["id"] == "q2"
    assert second["state"] == score.STOPPED, "stopped at the limit, not wrong"
    assert second["correct"] is None
    report = json.loads((folder / keeping.REPORT).read_text())
    assert report["stopped_at_limit"] is True
    assert report[score.STOPPED] == 1
    # A stopped run has its own figures too.
    assert report["figures"]["run"] == folder.name
    assert Path(report["figures"]["sheet"]).exists()
    out = capsys.readouterr().out
    assert f"Stopped at the limit of ${limit:.2f}" in out


def test_a_schema_and_reserve_past_the_limit_are_refused(stubs):
    """The rendered text alone fits the limit; with the schema and the output
    reserve, the request doesn't, and it's never sent."""
    _anthropic, openai = stubs
    openai.requests.clear()
    system, question = "Answer in JSON.", "What is the pet deposit?"
    schema = {
        "title": "plan",
        "type": "object",
        "properties": {"answer": {"type": "string", "description": "x" * 20_000}},
    }
    call = models.CloudCall(
        api.Purpose.REWRITE,
        SOL,
        system,
        [api.Part(api.PartKind.QUESTION, question)],
        models._render_tagged,  # noqa: SLF001  how a helper's request renders
        schema=schema,
    )
    price = api.price_of(SOL)
    assert price is not None
    text = len(json.dumps([system, question]).encode())
    text_alone = models.ceiling(text * 4, 0, price)  # four times over, and still fits
    limit = text_alone + 0.001
    assert models.ceiling(len(json.dumps(schema)), models.HELPER_MAX_TOKENS, price) > (
        limit
    )
    with models.spending_limit(limit), pytest.raises(models.OverLimit):
        asyncio.run(models.ask_cloud(call))
    assert openai.requests == [], "nothing was sent"


def charging_every_byte(stub, reply):
    """A provider that bills one token for every byte of the request as it arrived,
    and its whole output reserve: the most any of them can."""

    def charge(path: str, body: dict) -> dict:
        sent = len(stub.requests[-1].raw.encode())
        reserve = body.get("max_completion_tokens") or body.get("max_tokens") or 0
        answer = reply(path, body)
        if "choices" in answer:
            usage = {"prompt_tokens": sent, "completion_tokens": reserve}
            return {**answer, "usage": {**usage, "total_tokens": sent + reserve}}
        return {**answer, "usage": {"input_tokens": sent, "output_tokens": reserve}}

    return charge


def test_a_provider_charging_every_byte_cant_take_a_run_past_its_limit(
    lease, monkeypatch
):
    anthropic, openai = lease
    anthropic.reply = charging_every_byte(anthropic, judged)
    openai.reply = charging_every_byte(openai, TWO_CITED)
    _sol_helper(monkeypatch)
    third = {**FIRST, "id": "q3"}
    began = spend.spent_this_month()
    limit = 0.15
    report, _claims = run.evaluate(
        [FIRST, SECOND, third], SOL, cli.REPORTS, limit_usd=limit
    )
    assert report["stopped_at_limit"] is True, "the limit was met"
    assert spend.spent_this_month() - began <= limit


def test_no_run_that_may_spend_goes_without_a_limit(lease, monkeypatch):
    anthropic, openai = lease

    def never(*_given, **_named):
        raise AssertionError("the run started")

    monkeypatch.setattr(run, "_ask_all", never)
    for limit in (None, 0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(run.NoLimit):
            run.evaluate([QUESTION], SOL, limit_usd=limit)
    for given in ([], ["--limit-usd", "nan"], ["--limit-usd", "inf"]):
        with pytest.raises(SystemExit):
            cli.main(["--model", SOL, *given])
    assert anthropic.requests == openai.requests == []


def test_a_run_that_cant_spend_has_a_limit_of_nothing(stubs, monkeypatch):
    """A local unjudged run needs no limit, and runs under one of $0: not one cloud
    request could go from it."""
    _anthropic, openai = stubs
    openai.requests.clear()
    stored = run.settings.get

    def get(key):
        return run.settings.LOCAL if key == run.settings.MODEL_HELPER else stored(key)

    monkeypatch.setattr(run.settings, "get", get)
    limit = run._limit(run.settings.LOCAL, False, None)  # noqa: SLF001
    assert limit == 0.0
    call = models.CloudCall(
        api.Purpose.REWRITE,
        SOL,
        "Answer.",
        [api.Part(api.PartKind.QUESTION, "What is the pet deposit?")],
        models._render_tagged,  # noqa: SLF001  how a helper's request renders
    )
    with models.spending_limit(limit), pytest.raises(models.OverLimit):
        asyncio.run(models.ask_cloud(call))
    assert openai.requests == []


def test_requests_the_gate_cant_bound_never_go_on_a_limited_run(stubs, monkeypatch):
    """A page read and a key test don't pass the limit's gate, so on a limited run
    they're refused."""
    monkeypatch.setattr(api, "page_image_refusal", lambda _document_id: None)
    with models.spending_limit(100.0):
        with pytest.raises(models.CloudRefused, match="can't bound"):
            models.read_page_in_cloud("doc", 1, 1, b"png")
        with pytest.raises(models.CloudRefused, match="can't bound"):
            models.check_key(api.providers.OPENAI, "key")


def test_a_request_refused_as_it_leaves_is_logged_as_refused(stubs):
    """Through OpenAI's async client, a refusal raised as the request leaves comes
    back as it was, not as a connection error: it's logged as refused, at $0, not left
    at its estimate."""
    _anthropic, openai = stubs
    openai.requests.clear()
    call = models.CloudCall(
        api.Purpose.REWRITE,
        SOL,
        "Answer.",
        [api.Part(api.PartKind.QUESTION, "What is the pet deposit?")],
        models._render_tagged,  # noqa: SLF001  how a helper's request renders
        schema={"title": "plan", "type": "object", "properties": {}},
        still_wanted=lambda: False,
    )
    with pytest.raises(models.CloudRefused):
        asyncio.run(models.ask_cloud(call))
    with database.read() as connection:
        logged = connection.execute(
            sa.select(egress.egress_log.c.outcome, egress.egress_log.c.cost_usd)
        ).all()
    assert logged == [(egress.REFUSED, 0.0)] * 2, "both tries refused, nothing owed"
    assert openai.requests == []


def test_a_reply_with_no_usage_stays_counted_at_its_ceiling(stubs):
    """A stream that closes before its usage chunk: what it was billed is unknown, so
    the run keeps it at its ceiling, and a request that would pass the limit with it
    counted is refused."""
    _anthropic, openai = stubs
    openai.requests.clear()
    openai.reply = lambda _path, body: {**completion(body, "A summary."), "usage": None}
    call = models.CloudCall(
        api.Purpose.SUMMARY,
        SOL,
        "Summarize.",
        [api.Part(api.PartKind.TURN, "The pet deposit is $299.11.")],
        models._render_tagged,  # noqa: SLF001  how a helper's request renders
    )
    with models.spending_limit(100.0):
        asyncio.run(models.ask_cloud(call))
        limit = models._limit  # noqa: SLF001
        assert limit is not None
        (held,) = limit.out.values()
        assert held > 0
        assert limit.spent == 0.0
        limit.usd = held * 1.5  # the same request again would pass it, held counted
        with pytest.raises(models.OverLimit):
            asyncio.run(models.ask_cloud(call))
    assert len(openai.requests) == 1


def test_a_stream_cut_short_of_its_final_usage_stays_at_its_ceiling(stubs):
    """Anthropic's stream sends its input count first and its final usage with the
    stop reason, last. One that ends after its text, short of that, is billed for more
    than it says, so the run keeps it at its ceiling."""
    anthropic, _openai = stubs
    anthropic.requests.clear()

    def cut(path: str, body: dict) -> dict:
        usage = {"input_tokens": 50_000, "output_tokens": 5}
        return {**judged(path, body), "usage": usage, CUT_SHORT: True}

    anthropic.reply = cut
    call = models.CloudCall(
        api.Purpose.SUMMARY,
        "anthropic:claude-sonnet-5-5",
        "Summarize.",
        [api.Part(api.PartKind.TURN, "The pet deposit is $299.11.")],
        models._render_tagged,  # noqa: SLF001  how a helper's request renders
    )
    with models.spending_limit(100.0):
        asyncio.run(models.ask_cloud(call))
        limit = models._limit  # noqa: SLF001
        assert limit is not None
        (held,) = limit.out.values()
        assert held > 0
        assert limit.spent == 0.0
        limit.usd = held * 1.5
        with pytest.raises(models.OverLimit):
            asyncio.run(models.ask_cloud(call))
    assert len(anthropic.requests) == 1


def test_usage_is_trusted_only_from_a_reply_that_reached_its_stop():
    """A reply holding only the input count its stream began with, as a later
    LangChain may keep from Anthropic's message_start, isn't billed in full."""
    began = AIMessageChunk(
        content="partial",
        usage_metadata={"input_tokens": 50_000, "output_tokens": 1, "total_tokens": 1},
    )
    usage = models._usage_of(began)  # noqa: SLF001
    assert not models._billed_in_full(began, usage)  # noqa: SLF001
    for stop in ({"stop_reason": "end_turn"}, {"finish_reason": "stop"}):
        ended = AIMessageChunk(
            content="whole",
            usage_metadata=began.usage_metadata,
            response_metadata=stop,
        )
        assert models._billed_in_full(ended, usage)  # noqa: SLF001
    assert not models._billed_in_full(  # noqa: SLF001
        AIMessageChunk(content="", response_metadata={"finish_reason": "stop"}),
        {"input_tokens": 0, "output_tokens": 0},
    )
