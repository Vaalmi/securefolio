"""The figure check in Ask: every model's answer has
its figures checked after Verify, nothing is rewritten, the marks go in words into
the chat's content, a note, Copy and a follow-up, and Activity's line counts only."""

import json
import logging

import pytest
import sqlalchemy as sa

from agent import citations, harness
from agent.budgets import STANDARD
from backend import database, keychain, settings, sources, states
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OPENAI_ENV
from testing.ask_helpers import (
    ask,
    cited_reply,
    last_answer,
    message,
    new_thread,
    plan_reply,
    system_of,
)
from testing.queue import drain
from testing.stub_models import (
    completion,
    openai_reply,
    stub_anthropic,
    stub_openai,
)

LEASE = "Marker rent. The rent is due monthly at $2,350.\n"
# The year's sum is the model's own: the lease shows only the month's.
RENT = "The rent is $2,350 a month, or $28,200 a year."
UNSHOWN = "$28,200 (not in its sources)"


def claude_saying(text: str):
    def reply(_path: str, body: dict) -> dict:
        system = system_of(body)
        if "search query that stands alone" in system:
            return plan_reply(body)
        if "Keep what a later follow-up" in system:
            return message(body, [{"type": "text", "text": "A short summary."}])
        return cited_reply(body, text)

    return reply


@pytest.fixture
def lease(library, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "Lease.txt").write_text(LEASE)
    sources.add_source(str(root), states.STANDARD)
    drain()


@pytest.fixture
def cloud(monkeypatch):
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = claude_saying(RENT)
        yield stub


def figures_of(answer: dict) -> list[list[tuple[str, str]]]:
    return [
        [(f["text"], f["state"]) for f in part.get("figures") or []]
        for part in answer["parts"]
    ]


def answer_requests(stub) -> list[dict]:
    """The answer calls a stub received: their evidence is in search results."""
    return [
        json.loads(r.raw)
        for r in stub.requests
        if "search_result" in r.raw or '<source id=\\"' in r.raw
    ]


def test_claudes_answer_marks_the_figure_its_passage_doesnt_show(
    lease, cloud, app_client
):
    answer = last_answer(ask(app_client, new_thread(app_client), "What is the rent?"))
    assert figures_of(answer) == [[("$2,350", "shown"), ("$28,200", "unshown")]]
    (shown, _unshown) = answer["parts"][0]["figures"]
    assert (shown["cite"], shown["found"]) == (1, "$2,350")
    assert (
        "Checked 2 figures against their passages: 1 not in its sources"
        in answer["activity"]
    )


def test_nothing_is_rewritten_and_no_model_is_asked_again(
    lease, cloud, app_client, monkeypatch
):
    checked = last_answer(ask(app_client, new_thread(app_client), "What is the rent?"))
    asked = len(answer_requests(cloud))
    monkeypatch.setattr(harness.figures, "check", lambda parts, *_: [[] for _ in parts])
    unchecked = last_answer(
        ask(app_client, new_thread(app_client), "What is the rent?")
    )
    assert [p["text"] for p in checked["parts"]] == [
        p["text"] for p in unchecked["parts"]
    ]
    assert checked["citations"] == unchecked["citations"]
    assert len(answer_requests(cloud)) == 2 * asked, "one answer call each, no more"


def test_the_marks_go_in_words_into_content_copy_and_a_note(lease, cloud, app_client):
    found = ask(app_client, new_thread(app_client), "What is the rent?")
    saved = found["messages"][-1]
    assert UNSHOWN in saved["content"]
    copied = saved["copy_text"]
    assert f"{RENT.replace('$28,200', UNSHOWN)}[1]" in copied
    assert "[1] Lease, " in copied
    assert "(not in its sources): the passage its sentence cites doesn't show" in (
        copied
    )
    assert app_client.post(f"/api/messages/{saved['id']}/note").status_code == 200
    note = app_client.get("/api/notes").json()[0]
    assert UNSHOWN in note["body"]


def test_a_note_of_an_answer_saved_before_the_marks_gains_them(
    lease, cloud, app_client
):
    """The note is made from the answer's parts, as
    Copy is, not from the chat's saved text, which an answer saved before this work
    holds with no marks."""
    found = ask(app_client, new_thread(app_client), "What is the rent?")
    saved = found["messages"][-1]
    with database.write() as connection:
        connection.execute(
            sa.update(database.messages)
            .where(database.messages.c.id == saved["id"])
            .values(content=RENT)  # as saved before the marks
        )
    assert app_client.post(f"/api/messages/{saved['id']}/note").status_code == 200
    note = app_client.get("/api/notes").json()[0]
    assert UNSHOWN in note["body"]


def test_a_follow_up_reads_the_earlier_answer_with_its_marks(lease, cloud, app_client):
    thread = new_thread(app_client)
    ask(app_client, thread, "What is the rent?")
    ask(app_client, thread, "And the deposit?")
    follow_up = answer_requests(cloud)[-1]
    assert UNSHOWN in json.dumps(follow_up)


def test_a_marker_answer_is_checked_the_same_way(lease, app_client, monkeypatch):
    for provider in ("anthropic", "openai"):
        keychain.set_key(f"test-{provider}-key", provider)
    settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
    settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")

    def reply(path: str, body: dict) -> dict:
        if '<source id=\\"' not in json.dumps(body):
            return openai_reply(path, body)
        return completion(body, f"{RENT} [1]")

    with stub_openai() as openai:
        monkeypatch.setenv(STUB_OPENAI_ENV, openai.url)
        openai.reply = reply
        answer = last_answer(
            ask(app_client, new_thread(app_client), "What is the rent?")
        )
    assert figures_of(answer) == [[("$2,350", "shown"), ("$28,200", "unshown")]]


def test_the_log_carries_counts_never_the_answers_or_documents_words(
    lease, cloud, app_client, caplog
):
    with caplog.at_level(logging.INFO, logger="agent.harness"):
        ask(app_client, new_thread(app_client), "What is the rent?")
    (line,) = [
        r.getMessage() for r in caplog.records if "figures checked" in r.getMessage()
    ]
    assert line == 'figures checked: {"checked": 2, "unshown": 1}'
    said = "\n".join(r.getMessage() for r in caplog.records)
    for words in ("Marker rent", "due monthly", "$28,200", "$2,350", "a year"):
        assert words not in said


def test_the_check_runs_after_the_last_word_is_streamed(
    lease, cloud, app_client, monkeypatch
):
    """So the first words come no later: every token is out before the check."""
    order: list[str] = []
    stream, check = harness._stream_tokens, harness.figures.check  # noqa: SLF001

    def streaming():
        on_text = stream()

        async def recorded(text: str) -> None:
            order.append("token")
            await on_text(text)

        return recorded

    def checking(*given):
        order.append("check")
        return check(*given)

    monkeypatch.setattr(harness, "_stream_tokens", streaming)
    monkeypatch.setattr(harness.figures, "check", checking)
    ask(app_client, new_thread(app_client), "What is the rent?")
    assert "token" in order
    assert order.index("check") > max(i for i, e in enumerate(order) if e == "token")


def test_a_sentence_citing_a_card_is_checked_against_the_cards_text():
    """A list's cards are its evidence: a sentence citing one joins it by its element,
    so a figure the card doesn't show is marked."""
    row = {
        "document_id": "d",
        "revision_id": "r",
        "title": "Policy",
        "doc_type": "Insurance",
        "doc_date": "2026-01-01",
        "amount_text": "Premium $100",
        "summary": "Home cover.",
        "sensitivity": "standard",
    }
    items, unseen = harness._card_evidence({"rows": [row]}, STANDARD)  # noqa: SLF001
    assert unseen == 0
    (item,) = items
    built = citations.from_markers(
        "The premium is $200 [1]. It was $100 [1].", [(item, item["elements"][0])]
    )
    assert [c["element_id"] for c in built.citations] == ["card:d"]
    parts = harness._with_figures(built, items, None)  # noqa: SLF001
    assert figures_of({"parts": parts}) == [[("$200", "unshown")], [("$100", "shown")]]
