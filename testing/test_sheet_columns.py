"""A sheet's rows past its first chunk are handed over with the sheet's column names:
in Claude's search result title, in the `<source>` tag's `columns` attribute for
everyone else. The names are never quoted as the document's words. That a Private
sheet's names reach no cloud model is in `test_privacy_sheet_columns.py`."""

import pytest
import sqlalchemy as sa

from backend import database, keychain, search, settings, sources, states
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OPENAI_ENV
from testing.ask_helpers import ask, last_answer, new_thread, system_of
from testing.queue import drain
from testing.sheet_ledgers import PLAN, WANTED, add_ledgers, claude_finding, ledger
from testing.stub_models import (
    answers_of,
    completion,
    openai_reply,
    stub_anthropic,
    stub_openai,
)


@pytest.fixture
def sheets(library, tmp_path):
    return add_ledgers(tmp_path)


def _chunks_of(title: str) -> list[str]:
    with database.read() as connection:
        return list(
            connection.execute(
                sa.select(database.chunks.c.id)
                .join(
                    database.documents,
                    database.documents.c.id == database.chunks.c.document_id,
                )
                .where(database.documents.c.title == title)
                .order_by(database.chunks.c.id)
            ).scalars()
        )


def _element(element_id: str) -> dict:
    with database.read() as connection:
        row = (
            connection.execute(
                sa.select(database.elements).where(database.elements.c.id == element_id)
            )
            .mappings()
            .one()
        )
    return dict(row)


def _by_first_row(items: list[dict]) -> tuple[dict, dict]:
    """The chunk holding the header row, and the one after it."""
    first = next(i for i in items if i["elements"][0]["text"].startswith("Date"))
    later = next(i for i in items if i is not first)
    return first, later


class TestTheEvidence:
    def test_a_later_chunks_rows_get_the_column_names(self, sheets):
        items = search.evidence(_chunks_of("plain ledger"), include_private=False)
        assert len(items) == 2  # 41 rows: 25 and 16
        first, later = _by_first_row(items)
        assert "columns" not in first  # its first row is the names already
        assert later["columns"] == "Date, Payee, Amount"
        assert all("Date" not in e["text"] for e in later["elements"])

    def test_the_names_are_cut_as_the_heading_is(self, library, tmp_path):
        folder = tmp_path / "wide"
        folder.mkdir()
        wide = [f"Column number {n} of a very wide sheet" for n in range(200)]
        (folder / "wide.csv").write_text(ledger(wide))
        sources.add_source(str(folder), states.STANDARD)
        drain()
        items = search.evidence(_chunks_of("wide"), include_private=False)
        names = next(i["columns"] for i in items if "columns" in i)
        assert names.startswith("Column number 0 of a very wide sheet, ")
        assert search.tokens.count(names) <= search.COLUMN_TOKENS
        assert len(names) < len(", ".join(wide))

    def test_a_document_that_isnt_a_sheet_gets_none(self, library, tmp_path):
        folder = tmp_path / "plain"
        folder.mkdir()
        (folder / "Lease.txt").write_text("The rent is due monthly.\n")
        sources.add_source(str(folder), states.STANDARD)
        drain()
        items = search.evidence(_chunks_of("Lease"), include_private=False)
        assert items
        assert all("columns" not in i for i in items)


@pytest.fixture
def anthropic(monkeypatch):
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = claude_finding(WANTED)
        yield stub
    keychain.delete_key()


def _search_results(stub) -> list[dict]:
    return [
        block
        for request in stub.requests
        for message in request.body.get("messages", [])
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "search_result"
    ]


def _assert_quotes_the_row(citation: dict) -> None:
    """The quote and the highlight are the row's own: its text and its cells."""
    row = _element(citation["element_id"])
    assert citation["quote"] == row["text"]
    assert citation["locator"] == row["locator"]
    assert "Columns" not in citation["quote"]
    assert "Date" not in citation["quote"]


class TestClaude:
    def test_the_names_go_in_the_title_and_the_quote_stays_the_rows(
        self, sheets, anthropic, app_client
    ):
        answer = last_answer(
            ask(app_client, new_thread(app_client), "Who is Larkspur?")
        )
        titles = [block["title"] for block in _search_results(anthropic)]
        assert "plain ledger · Columns: Date, Payee, Amount" in titles
        cited = answer["citations"][0]
        assert cited["title"] == "plain ledger"
        _assert_quotes_the_row(cited)


class TestMarkers:
    @pytest.fixture
    def openai(self, library, monkeypatch):
        keychain.set_key("test-openai-key", "openai")
        with stub_openai() as stub:
            monkeypatch.setenv(STUB_OPENAI_ENV, stub.url)

            def reply(path: str, body: dict) -> dict:
                if "stands alone" in system_of(body):
                    return completion(body, PLAN)
                return openai_reply(path, body)

            stub.reply = reply
            yield stub
        keychain.delete_key("openai")

    def test_the_names_go_in_the_tag_and_the_quote_stays_the_rows(
        self, sheets, openai, app_client
    ):
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        settings.put(settings.MODEL_HELPER, "openai:gpt-6.1-sol")
        answer = last_answer(
            ask(app_client, new_thread(app_client), "Who is Larkspur?")
        )
        sent = "\n".join(r.raw for r in answers_of(openai))
        assert 'columns=\\"Date, Payee, Amount\\"' in sent
        cited = answer["citations"][0]
        _assert_quotes_the_row(cited)
