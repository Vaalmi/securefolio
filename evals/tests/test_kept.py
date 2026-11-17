"""Kept runs: one folder per run, each move written once, nothing changed once it ends;
each row holds the answer as written, what it was handed, and its times."""

import asyncio
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent import models
from backend import auth
from backend.build_mode import STUB_OLLAMA_ENV
from backend.main import create_app
from evals import cli, run
from evals import kept as keeping
from evals.tests.fake_runs import fake_evaluate
from evals.tests.kept_runs import own
from evals.tests.questions import PLANNED, QUESTION, SOL
from evals.tests.stub_providers import answering_with
from testing.stub_models import ollama_reply, stub_ollama


def test_a_run_is_kept_in_a_folder_of_its_own(tmp_path):
    first = keeping.start(tmp_path, "openai:gpt-6.1-sol", {"questions": 1})
    second = keeping.start(tmp_path, "openai:gpt-6.1-sol", {"questions": 1})
    assert first.folder != second.folder, "a run never overwrites another"
    assert first.state == keeping.RUNNING
    assert first.manifest["questions"] == 1


def test_each_question_has_one_row(tmp_path):
    kept = keeping.start(tmp_path, "m", {})
    kept.row({"id": "q1"})
    with pytest.raises(keeping.AlreadyRecorded):
        kept.row({"id": "q1"})
    assert [row["id"] for row in kept.rows()] == ["q1"]


def test_a_run_finishes_only_with_every_row(tmp_path):
    kept = keeping.start(tmp_path, "m", {})
    kept.row({"id": "q1"})
    with pytest.raises(ValueError, match="q2"):
        kept.finish(own(kept, questions=2), ["q1", "q2"])
    kept.row({"id": "q2"})
    kept.finish(own(kept, questions=2), ["q1", "q2"])
    assert kept.state == keeping.FINISHED
    assert kept.report() == own(kept, questions=2)


def test_a_run_that_cant_finish_is_kept_as_failed_not_running(tmp_path):
    """A run missing a row is failed, never left running."""
    kept = keeping.start(tmp_path, "m", {})
    with pytest.raises(ValueError, match="no row for q1"):
        cli._finish({"kept": str(kept.folder)}, [{"id": "q1"}], tmp_path, False)  # noqa: SLF001
    assert keeping.Kept(kept.folder).state == keeping.FAILED


@pytest.mark.parametrize("end", ["finish", "stop", "fail"])
def test_an_ended_run_never_changes(tmp_path, end):
    kept = keeping.start(tmp_path, "m", {})
    if end == "finish":
        kept.finish(own(kept), [])
    elif end == "stop":
        kept.stop_at_limit(own(kept))
    else:
        kept.fail("RuntimeError")
    with pytest.raises(keeping.RunEnded):
        kept.row({"id": "q1"})
    with pytest.raises(keeping.RunEnded):
        kept.finish({}, [])
    with pytest.raises(keeping.RunEnded):
        kept.fail("again")


@pytest.mark.parametrize("end", ["finish", "stop"])
@pytest.mark.parametrize("figures", ["none", "another run's"])
def test_a_run_never_ends_with_figures_not_its_own(tmp_path, end, figures):
    """A report with no figures
    section, or another run's, can't end a run. The run is failed, never left running
    or kept with the wrong figures."""
    other = keeping.start(tmp_path, "m", {})
    report = {} if figures == "none" else own(other)
    kept = keeping.start(tmp_path, "m", {})
    ending = kept.stop_at_limit if end == "stop" else partial(kept.finish, asked=[])
    with pytest.raises(keeping.NotItsOwnFigures):
        ending(report)
    assert kept.state == keeping.FAILED
    assert kept.report() is None


def test_only_finished_runs_are_read_back(tmp_path):
    done = keeping.start(tmp_path, "a", {})
    done.finish(own(done), [])
    keeping.start(tmp_path, "b", {}).fail("RuntimeError")
    keeping.start(tmp_path, "c", {})
    assert [k.folder for k in keeping.finished_runs(tmp_path)] == [done.folder]


def test_the_times_come_from_the_runs_events(monkeypatch):
    # Events land at known moments on a clock of the test's own, which only the
    # polling's sleep moves on, so each time is read to within one poll of its
    # event, however slow the machine running the test.
    clock = {"now": 0.0}
    landing = ((0.1, "step"), (0.3, "token"))
    finished_at = 0.5

    def events_after(_run, after):
        landed = [name for at, name in landing if at <= clock["now"]]
        return [
            {"id": i + 1, "event": name, "data": {}} for i, name in enumerate(landed)
        ][after:]

    def get_run(_run):
        return {"state": "completed" if clock["now"] >= finished_at else "running"}

    def sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(run.threads, "events_after", events_after)
    monkeypatch.setattr(run.threads, "get_run", get_run)
    monkeypatch.setattr(
        run, "time", SimpleNamespace(monotonic=lambda: clock["now"], sleep=sleep)
    )
    times = run._timed("run", 0.0)  # noqa: SLF001
    poll_ms = round(run.POLL_SECONDS * 1000)
    for name, at in (
        ("first_step_ms", 100),
        ("first_words_ms", 300),
        ("complete_ms", 500),
    ):
        measured = times[name]
        assert measured is not None
        assert at <= measured <= at + poll_ms, (name, measured)


def test_a_run_that_never_finishes_has_no_complete_time(monkeypatch):
    monkeypatch.setattr(run, "WAIT_SECONDS", 0.05)
    monkeypatch.setattr(run.threads, "events_after", lambda _r, _a: [])
    monkeypatch.setattr(run.threads, "get_run", lambda _r: {"state": "running"})
    times = run._timed("run", time.monotonic())  # noqa: SLF001
    assert times == {"first_step_ms": None, "first_words_ms": None, "complete_ms": None}


def test_events_written_just_before_the_finish_are_still_timed(monkeypatch):
    """The run writes its last events between the
    read and the state check, and finishes."""
    reads = iter([[], [{"id": 1, "event": "step"}, {"id": 2, "event": "token"}]])
    monkeypatch.setattr(run.threads, "events_after", lambda _r, _a: next(reads, []))
    monkeypatch.setattr(run.threads, "get_run", lambda _r: {"state": "completed"})
    times = run._timed("run", time.monotonic())  # noqa: SLF001
    assert times["first_step_ms"] is not None
    assert times["first_words_ms"] is not None
    assert times["complete_ms"] is not None


def test_a_thrown_away_reply_is_kept_with_what_it_was_handed(lease, tmp_path):
    # No passage 99 was handed, so the answer is thrown away for its citations.
    _anthropic, openai = lease
    openai.reply = answering_with("The pet deposit is $299.11. [99]")
    report, _claims = run.evaluate([QUESTION], SOL, tmp_path / "runs", limit_usd=50.0)
    kept = keeping.Kept(Path(report["kept"]))
    (row,) = kept.rows()
    assert row["kind"] == "no_evidence"
    assert row["thrown_away"] is True
    assert row["written"]["text"] == "The pet deposit is $299.11. [99]"
    assert row["written"]["cites"] == "markers"
    handed = [e["text"] for p in row["written"]["handed"] for e in p["elements"]]
    assert any("299.11" in text for text in handed), "the passage it was handed"
    assert row["first_step_ms"] <= row["first_words_ms"] <= row["complete_ms"]
    manifest = kept.manifest
    assert manifest["answer_model"] == SOL
    assert manifest["answer_version"] == SOL
    assert manifest["index"]["revisions"] >= 1
    assert "answer.md" in manifest["instructions"]
    assert manifest["question_set"] == report["question_set"]


def test_the_models_own_refusal_isnt_thrown_away(lease, tmp_path):
    # The refusal written with its stop was once counted as thrown away, and an
    # answerable question it refused was put at citations, not at answer or refuse.
    _anthropic, openai = lease
    openai.reply = answering_with("I couldn't find enough evidence in this scope.")
    report, _claims = run.evaluate([QUESTION], SOL, tmp_path / "runs", limit_usd=50.0)
    (row,) = keeping.Kept(Path(report["kept"])).rows()
    assert row["kind"] == "no_evidence"
    assert row["thrown_away"] is False
    assert row["step"] == "answer_or_refuse", "it refused with the fact in hand"
    assert report["thrown_away"] == 0


def test_an_answer_that_reads_is_kept_as_answered(lease, tmp_path):
    report, _claims = run.evaluate([QUESTION], SOL, tmp_path / "runs", limit_usd=50.0)
    (row,) = keeping.Kept(Path(report["kept"])).rows()
    assert row["state"] == "answered"
    assert row["thrown_away"] is False
    assert row["citations"]
    assert row["citations"][0]["title"] == "Apartment lease"
    assert row["claims"]
    assert row["claims"][0]["supported"] is True
    # Each judged claim keeps
    # its document by id, so judging it again never has to tell it from its text.
    handed = row["written"]["handed"]
    lease_ids = {e["document_id"] for e in handed if e["title"] == "Apartment lease"}
    assert row["claims"][0]["document_id"] in lease_ids


def test_the_record_is_the_newest_turns(lease):
    # A second turn in the same thread replaces the first's record.
    _anthropic, openai = lease
    app = create_app(run.PORT, auth.new_tokens(), write_runtime=False)
    headers = {"Authorization": f"Bearer {app.state.tokens.app}"}
    with (
        TestClient(app, base_url=run.HOST, headers=headers) as client,
        run._answering(SOL),  # noqa: SLF001
    ):
        thread = client.post("/api/threads", json={"scope": None}).json()["id"]
        ids = []
        for said in ("First [1].", "Second [1]."):
            openai.reply = answering_with(said)
            started = client.post(
                f"/api/threads/{thread}/messages", json={"text": QUESTION["question"]}
            ).json()
            run._timed(started["id"], time.monotonic())  # noqa: SLF001
            ids.append(started["id"])
        record, portal = app.state.agent.turn_record, client.portal
        assert portal is not None
        assert portal.call(record, thread, ids[0]) is None
        assert portal.call(record, thread, ids[1])["text"] == "Second [1]."


def test_each_call_is_recorded_once_with_its_row(lease, tmp_path):
    report, _claims = run.evaluate([PLANNED], SOL, tmp_path / "runs", limit_usd=50.0)
    (row,) = keeping.Kept(Path(report["kept"])).rows()
    purposes = [call["purpose"] for call in row["calls"]]
    assert purposes.count("rewrite") == 1, "the plan"
    assert purposes.count("answer") == 1
    assert purposes.count("eval_judge") >= 1
    answer = next(call for call in row["calls"] if call["purpose"] == "answer")
    assert answer["model"] == SOL
    assert answer["outcome"] == "sent"
    assert answer["input_tokens"] > 0
    assert answer["duration_ms"] >= 0
    assert all("text" not in call for call in row["calls"]), "counts, never text"


def test_nothing_is_recorded_outside_the_evaluation(lease):
    assert models.take_calls("any") == []
    with models.recording_calls():
        pass
    assert models._calls is None  # noqa: SLF001


def test_a_cloud_call_refused_before_sending_is_recorded(monkeypatch):
    """A call refused as it's admitted, before any
    model is built, still has its record, as one refused as it leaves does."""
    monkeypatch.setattr(models.api.policy, "key_ready", lambda _provider: True)
    monkeypatch.setattr(models.api, "refusal", lambda _parts, _thread: "offline")
    monkeypatch.setattr(models.api, "log_refused", lambda *_a, **_k: None)
    call = models.CloudCall(
        models.api.Purpose.ANSWER, SOL, "Answer.", [], lambda _parts: [], "t"
    )
    with models.recording_calls():
        with pytest.raises(models.CloudRefused):
            asyncio.run(models.ask_cloud(call))
        (record,) = models.take_calls("t")
    assert (record["outcome"], record["model"]) == ("refused", SOL)
    assert record["purpose"] == "answer"


def test_a_cloud_call_with_no_key_is_recorded_as_failed(monkeypatch):
    """The key went after the
    thread was routed, so nothing is sent and the local model stands in, and the
    cloud attempt still has its record, as one with no network does."""
    monkeypatch.setattr(models.api.policy, "key_ready", lambda _provider: False)
    call = models.CloudCall(
        models.api.Purpose.ANSWER, SOL, "Answer.", [], lambda _parts: [], "t"
    )
    with models.recording_calls():
        with pytest.raises(models.NoConnection):
            asyncio.run(models.ask_cloud(call))
        (record,) = models.take_calls("t")
    assert (record["outcome"], record["model"], record["cost_usd"]) == (
        "failed",
        SOL,
        0.0,
    )


LOCAL_TAG = "gemma4:26b"
SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}
LOCAL_CALLS = {
    "answer": lambda: models.ask_local(
        "Answer.", "Q", tag=LOCAL_TAG, of=models.CallOf("t", fit=models.Fit(9, 1))
    ),
    "structured": lambda: models.structured(
        models.Task(
            models.api.Purpose.REWRITE, "Plan.", [], "t", True, local_tag=LOCAL_TAG
        ),
        SCHEMA,
    ),
    "text": lambda: models.helper_text(
        models.Task(
            models.api.Purpose.SUMMARY, "Sum.", [], "t", True, local_tag=LOCAL_TAG
        )
    ),
}


@pytest.mark.parametrize("kind", LOCAL_CALLS)
def test_a_local_call_whose_model_isnt_ready_is_recorded_as_failed(monkeypatch, kind):
    """Ollama stopped listing the model
    after routing, so it fails as it's built, before any request, and still has its
    record, as a cloud call refused before sending does."""
    monkeypatch.setattr(models, "tag_listed", lambda _tag: False)
    with models.recording_calls():
        with pytest.raises(models.LocalNotReady):
            asyncio.run(LOCAL_CALLS[kind]())
        (record,) = models.take_calls("t")
    assert (record["outcome"], record["model"]) == ("failed", LOCAL_TAG)
    assert "prompt_counted" not in record, "no prompt went, so not a local answer"


def test_a_local_answer_retried_without_thinking_is_two_records(monkeypatch):
    replies = iter([("", "length"), ("The deposit is $299.11 [1].", "stop")])

    def chat(path: str, body: dict) -> dict:
        if path != "/api/chat":
            return ollama_reply(path, body)
        text, why = next(replies)
        return {
            "model": body["model"],
            "message": {"role": "assistant", "content": text},
            "done": True,
            "done_reason": why,
            "prompt_eval_count": 7,
            "eval_count": 3,
        }

    with stub_ollama() as local, models.recording_calls():
        local.reply = chat
        monkeypatch.setenv(STUB_OLLAMA_ENV, local.url)
        monkeypatch.setattr(models, "tag_listed", lambda _tag: True)
        reply = asyncio.run(
            models.ask_local("Answer.", "Q", tag="gemma4:26b", of=models.CallOf("t"))
        )
        calls = models.take_calls("t")
    assert reply.text == "The deposit is $299.11 [1]."
    assert [call["outcome"] for call in calls] == ["retried", "sent"]
    assert all(call["model"] == "gemma4:26b" for call in calls)


def test_an_uncited_sentence_keeps_its_verdict(monkeypatch):
    monkeypatch.setattr(run.models, "judge_sentence", lambda *_a, **_k: False)
    answer = {
        "parts": [{"text": "Every policy renews in March.", "uncited": True}],
        "citations": [],
        "table": {
            "rows": [
                {
                    "title": "Policy",
                    "amount_text": "$10.00",
                    "summary": "A policy.",
                    "document_id": "d",
                    "sensitivity": "standard",
                }
            ]
        },
    }
    claims, verdicts, sound = run._judged(answer)  # noqa: SLF001
    assert claims == [
        {"claim": "Every policy renews in March.", "uncited": True, "supported": False}
    ]
    assert verdicts == [], "an uncited sentence isn't a citation"
    assert sound is False
    assert run.sample(claims, 5) == [], "the hand check is of citations"


def test_the_manifest_holds_the_local_models_setup(monkeypatch):
    with stub_ollama() as local:
        monkeypatch.setenv(STUB_OLLAMA_ENV, local.url)
        found = models.local_setup("gemma4:26b")
    assert found["ollama"] == "0.0.0-stub"
    assert found["details"]["quantization_level"] == "Q4_K_M"
    assert found["parameters"] == "temperature 1"
    assert found["trained_context"] == 131072
    assert found["loaded_context"] is None, "not loaded, so Ollama says nothing"


def test_a_local_helpers_tokens_are_recorded(monkeypatch):
    def chat(path: str, body: dict) -> dict:
        if path != "/api/chat":
            return ollama_reply(path, body)
        return {
            "model": body["model"],
            "message": {"role": "assistant", "content": '{"query": "pet deposit"}'},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 7,
            "eval_count": 3,
        }

    task = models.Task(
        models.api.Purpose.REWRITE, "Plan.", [], "t", True, local_tag="gemma4:26b"
    )
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    with stub_ollama() as local, models.recording_calls():
        local.reply = chat
        monkeypatch.setenv(STUB_OLLAMA_ENV, local.url)
        monkeypatch.setattr(models, "tag_listed", lambda _tag: True)
        found = asyncio.run(models.structured(task, schema))
        (call,) = models.take_calls("t")
    assert found == {"query": "pet deposit"}
    assert call["purpose"] == "rewrite"
    assert (call["input_tokens"], call["output_tokens"]) == (7, 3)
    assert call["outcome"] == "sent"


def test_the_context_ollama_gave_reaches_the_manifest(tmp_path, monkeypatch):
    loaded = {"tag": "gemma4:26b", "loaded_context": 32768}
    found = {"questions": 1, "local_loaded": loaded}
    monkeypatch.setattr(cli, "REPORTS", tmp_path)
    monkeypatch.setattr(cli.run, "evaluate", fake_evaluate(found))
    cli.main(["--skip-hard-pages", "--limit-usd", "50"])
    (kept,) = keeping.finished_runs(tmp_path / cli.RUNS)
    assert kept.manifest["local_loaded"] == loaded
