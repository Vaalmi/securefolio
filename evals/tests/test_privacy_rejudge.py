"""A kept run judged again: a Private answer no judge read is kept as it was, never sent
to the new judge, and doesn't stop the run before it starts."""

import pytest
import sqlalchemy as sa

from agent import models
from backend import agent_service, database, states
from evals import compare, rejudge
from evals import kept as keeping
from evals.tests.judged_runs import (
    TERRA,
    handed,
    judged_row,
    judged_run,
    openai_judging,
)
from evals.tests.kept_runs import SET, answer
from evals.tests.questions import PLANNED


def test_a_private_row_no_judge_read_doesnt_stop_the_run_and_isnt_sent(
    lease, tmp_path, monkeypatch
):
    # As gemma4:26b's closing run keeps q25: a Private answer citing its passage, with
    # no claims, since no judge read it. Its sentences can't line up with claims.
    anthropic, openai = lease
    anthropic.requests.clear()
    openai.requests.clear()
    openai.reply = openai_judging(True)
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    monkeypatch.setattr(rejudge, "_question", lambda _id: PLANNED)
    private = answer(
        "q25",
        True,
        category="private",
        private=True,
        correct=None,
        sentences=[{"text": "The refund due was $4,561.96. ", "cite": [1]}],
        citations=[{"n": 1, "title": "Federal return 2024"}],
        claims=[],
        model="gemma4:26b",
        written={"handed": [], "table": None},
    )
    old = keeping.Kept(judged_run(tmp_path / "old", [judged_row(), private]))
    with agent_service.judged_by(TERRA):
        new, agreement = rejudge.rejudge(old, tmp_path / "new", TERRA, 1.0)
    assert anthropic.requests == []
    assert len(openai.requests) == 2, "the judged row's two claims, and only those"
    assert all("4,561.96" not in r.raw for r in openai.requests)
    assert agreement["claims"] == 2
    kept = new.rows()[1]
    assert (kept["id"], kept["claims"], kept["correct"]) == ("q25", [], None)


DEPOSIT = "A pet deposit of $299.11."
REFUSED = "keeps no document, and its title names none or several"


def sharing_a_title(kept_id: bool) -> tuple[dict, str]:
    """A row citing "Notice", handed two indexed documents of that title, the second
    the one it quotes; its claim keeps that one's id if `kept_id`, as claims do now.
    And the second's id."""
    with database.read() as connection:
        first, second = connection.execute(
            sa.select(database.documents.c.id)
            .order_by(database.documents.c.title)
            .limit(2)
        ).scalars()
    claim = {
        "claim": "The deposit is $299.11.",
        "title": "Notice",
        "words": "$299.11",
        "passage": DEPOSIT,
        "supported": True,
    }
    row = answer(
        "q1",
        True,
        sentences=[{"text": "The deposit is $299.11. ", "cite": [1]}],
        citations=[{"n": 1, "title": "Notice"}],
        claims=[{**claim, "document_id": second} if kept_id else claim],
        model="gemma4:26b",
        written={
            "handed": handed((first, "Notice", DEPOSIT), (second, "Notice", DEPOSIT)),
            "table": None,
        },
    )
    return row, second


PRIVATE_NOW = "which is Private now, so nothing of it is sent"


def made_private(document: str) -> None:
    with database.write() as connection:
        connection.execute(
            sa.update(database.documents)
            .where(database.documents.c.id == document)
            .values(sensitivity=states.PRIVATE)
        )


def test_a_claim_keeping_a_now_private_document_is_refused_before_any_call(
    lease, tmp_path, monkeypatch
):
    """A claim's document was once told from its title and its text, and could be told
    wrong. It keeps its document's id now. Here two documents share its title and both
    hold its passage, and the one it keeps is Private now: the re-judge refuses before
    anything is sent."""
    _anthropic, openai = lease
    openai.requests.clear()
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    row, quoted = sharing_a_title(kept_id=True)
    made_private(quoted)
    old = keeping.Kept(judged_run(tmp_path / "old", [row]))
    with pytest.raises(rejudge.CantRejudge, match=PRIVATE_NOW):
        rejudge.rejudge(old, tmp_path / "new", TERRA, 1.0)
    assert openai.requests == []
    assert not (tmp_path / "new").exists(), "refused before the new run starts"


def test_an_uncited_sentences_sources_keep_each_document_giving_the_same_words():
    """Two citations of one title and
    one passage, from two documents, were once kept as one source, the first's."""
    common = {"title": "Notice", "words": "$100", "passage": "The fee is $100."}
    row = {
        "claims": [{**common, "document_id": "A"}, {**common, "document_id": "B"}],
        "written": {"table": None},
    }
    found = rejudge._sources(row)  # noqa: SLF001
    assert [source.document_id for source in found] == ["A", "B"]


@pytest.mark.parametrize("cited", [True, False])
def test_a_row_resting_on_a_now_private_document_is_refused_before_any_call(
    lease, tmp_path, monkeypatch, cited
):
    """The same row: an uncited sentence, judged against the answer's sources, then
    two citations giving the same words from two documents of one title, the second
    Private now. Refused before anything is sent, whatever way its words would take:
    and so too when the Private one was only handed to the answer, never cited."""
    _anthropic, openai = lease
    openai.requests.clear()
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    with database.read() as connection:
        first, second = connection.execute(
            sa.select(database.documents.c.id)
            .order_by(database.documents.c.title)
            .limit(2)
        ).scalars()
    made_private(second)
    claim = {
        "claim": "The deposit is $299.11.",
        "title": "Notice",
        "words": "$299.11",
        "passage": DEPOSIT,
        "supported": True,
    }
    claims = [{**claim, "document_id": first}]
    if cited:
        claims.append({**claim, "document_id": second})
    row = answer(
        "q1",
        True,
        sentences=[
            {"text": "It is paid at signing. ", "uncited": True},
            {"text": "The deposit is $299.11. ", "cite": [1, 2] if cited else [1]},
        ],
        citations=[{"n": 1, "title": "Notice"}, {"n": 2, "title": "Notice"}],
        claims=[
            {"claim": "It is paid at signing.", "uncited": True, "supported": True},
            *claims,
        ],
        model="gemma4:26b",
        written={
            "handed": handed((first, "Notice", DEPOSIT), (second, "Notice", DEPOSIT)),
            "table": None,
        },
    )
    old = keeping.Kept(judged_run(tmp_path / "old", [row]))
    with pytest.raises(rejudge.CantRejudge, match=PRIVATE_NOW):
        rejudge.rejudge(old, tmp_path / "new", TERRA, 1.0)
    assert openai.requests == []
    assert not (tmp_path / "new").exists(), "refused before the new run starts"


KEY_FEE = "A key fee of $45.50."


def test_a_document_made_private_while_the_run_goes_stops_it_at_its_next_call(
    lease, tmp_path, monkeypatch
):
    """The check before the run reads sensitivity as
    it is then. A second row rests on a document only handed to its answer, never
    cited, made Private while the first row is judged: each call names every
    document its row rests on, so policy refuses the second row's first call, and
    none of its words leave."""
    _anthropic, openai = lease
    openai.requests.clear()
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    monkeypatch.setattr(rejudge, "_question", lambda _id: PLANNED)
    with database.read() as connection:
        first, second = connection.execute(
            sa.select(database.documents.c.id)
            .order_by(database.documents.c.title)
            .limit(2)
        ).scalars()
    judging = openai_judging(True)

    def making_it_private(path: str, body: dict) -> dict:
        made_private(second)  # as the first row is judged
        return judging(path, body)

    openai.reply = making_it_private
    later = answer(
        "q2",
        True,
        sentences=[
            {"text": "The fee is due at signing. ", "uncited": True},
            {"text": "The key fee is $45.50. ", "cite": [1]},
        ],
        citations=[{"n": 1, "title": "Notice"}],
        claims=[
            {"claim": "The fee is due at signing.", "uncited": True, "supported": True},
            {
                "claim": "The key fee is $45.50.",
                "title": "Notice",
                "words": "$45.50",
                "passage": KEY_FEE,
                "document_id": first,
                "supported": True,
            },
        ],
        model="gemma4:26b",
        written={
            "handed": handed((first, "Notice", KEY_FEE), (second, "Other", "Fees.")),
            "table": None,
        },
    )
    old = keeping.Kept(judged_run(tmp_path / "old", [judged_row(), later]))
    with (
        agent_service.judged_by(TERRA),
        pytest.raises(models.CloudRefused, match="holds something Private"),
    ):
        rejudge.rejudge(old, tmp_path / "new", TERRA, 1.0)
    sent = [r.raw for r in openai.requests]
    assert sent, "the first row was judged"
    assert all("due at signing" not in raw and "45.50" not in raw for raw in sent)


def test_a_claim_kept_with_no_id_whose_title_two_share_is_refused_before_any_call(
    lease, tmp_path, monkeypatch
):
    _anthropic, openai = lease
    openai.requests.clear()
    monkeypatch.setattr(compare, "calibration_set", lambda: SET)
    row, _quoted = sharing_a_title(kept_id=False)
    old = keeping.Kept(judged_run(tmp_path / "old", [row]))
    with pytest.raises(rejudge.CantRejudge, match=REFUSED):
        rejudge.rejudge(old, tmp_path / "new", TERRA, 1.0)
    assert openai.requests == []
    assert not (tmp_path / "new").exists(), "refused before the new run starts"


@pytest.mark.parametrize(
    "elements",
    [
        # A fragment in one, the passage across two
        # elements of the other.
        (["A pet deposit"], ["Lease terms: A pet deposit of", "$299.11 is due."]),
        # The passage cut for the model in the one it
        # quotes, and whole in the other.
        (["A pet deposit of $299.11 is due Friday."], ["A pet deposit of $299.11"]),
        (["A pet deposit of $299.11."], ["Other words."]),
        (["A pet deposit of $299.11."], ["A pet deposit of $299.11."]),
    ],
)
def test_where_two_share_a_title_no_text_tells_a_claim_kept_with_no_id(elements):
    claim = {
        "title": "Notice",
        "words": "$299.11",
        "passage": "A pet deposit of $299.11 is due Friday.",
    }
    row = {
        "id": "q1",
        "written": {
            "handed": [
                {
                    "document_id": document,
                    "title": "Notice",
                    "elements": [{"text": text} for text in texts],
                }
                for document, texts in zip(("a", "b"), elements, strict=True)
            ],
            "table": None,
        },
    }
    with pytest.raises(rejudge.CantRejudge, match=REFUSED):
        rejudge._document(claim, row)  # noqa: SLF001
    assert rejudge._document({**claim, "document_id": "b"}, row) == "b"  # noqa: SLF001


def test_one_document_of_its_title_is_the_claims_own():
    claim = {"title": "Lease", "words": "$5", "passage": "A summary of it."}
    row = {
        "id": "q1",
        "written": {
            "handed": handed(("lease", "Lease", "The whole lease.")),
            "table": None,
        },
    }
    assert rejudge._document(claim, row) == "lease"  # noqa: SLF001
    row["written"]["handed"] = handed(("other", "Other", "A summary of it."))
    with pytest.raises(rejudge.CantRejudge, match=REFUSED):
        rejudge._document(claim, row)  # noqa: SLF001
