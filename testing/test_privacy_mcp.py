"""MCP sees only Standard folders with their switch on, read fresh each call, and
changes nothing. Private documents, passages, neighbors and collections never appear."""

import asyncio
import hashlib
import json

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend import (
    cards,
    collections,
    database,
    mcp_server,
    search,
    sources,
    states,
    vault,
)
from backend.main import create_app
from conftest import LOOPBACK, TEST_PORT, TEST_TOKENS
from testing.queue import drain

HEADERS = {
    "Authorization": f"Bearer {TEST_TOKENS.mcp}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


@pytest.fixture
def worlds(library, tmp_path):
    open_folder = tmp_path / "Open"
    open_folder.mkdir()
    long_text = "\n\n".join(
        f"Paragraph {n} of the open lease says rent marker bluefin {n}. "
        + "word " * 300
        for n in range(4)
    )
    (open_folder / "Open lease.txt").write_text(long_text)
    shut = tmp_path / "Shut"
    shut.mkdir()
    (shut / "Shut lease.txt").write_text("The shut lease says rent marker bluefin.\n")
    secret = tmp_path / "Secret"
    secret.mkdir()
    (secret / "Secret lease.txt").write_text("The secret rent marker bluefin zqPRIV.\n")
    found = {
        "open": sources.add_source(str(open_folder), states.STANDARD, mcp_enabled=True),
        "shut": sources.add_source(str(shut), states.STANDARD, mcp_enabled=False),
        "secret": sources.add_source(str(secret), states.PRIVATE),
    }
    drain()
    return found


@pytest.fixture
def client():
    app = create_app(TEST_PORT, TEST_TOKENS, write_runtime=False)
    with TestClient(app, base_url=LOOPBACK) as made:
        yield made


def call(client: TestClient, name: str, **arguments) -> dict:
    answer = client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert answer.status_code == 200, answer.text
    return answer.json()["result"]


def payload(result: dict):
    assert not result.get("isError"), result
    if "structuredContent" in result:
        structured = result["structuredContent"]
        return structured.get("result", structured)
    return json.loads(result["content"][0]["text"])


def document_titled(title: str) -> str:
    with database.read() as connection:
        return connection.execute(
            sa.select(database.documents.c.id).where(
                database.documents.c.title == title
            )
        ).scalar_one()


def test_no_tool_has_an_optional_input():
    def check(schema: dict) -> None:
        if isinstance(schema, dict):
            properties = schema.get("properties")
            if properties is not None:
                assert set(schema.get("required", [])) == set(properties)
            for value in schema.values():
                if isinstance(value, dict):
                    check(value)
                elif isinstance(value, list):
                    for item in value:
                        check(item)

    tools = asyncio.run(mcp_server.build().list_tools())
    assert {tool.name for tool in tools} == {
        "search_library",
        "read_passage",
        "get_document",
        "list_collections",
    }
    for tool in tools:
        check(tool.input_schema)


def test_search_finds_only_what_is_open(worlds, client):
    found = payload(call(client, "search_library", query="rent bluefin", limit=10))
    assert found
    assert {hit["title"] for hit in found} == {"Open lease"}
    assert "zqPRIV" not in json.dumps(found)


def test_a_passage_comes_with_its_neighbors_in_one_document(worlds, client):
    hits = payload(call(client, "search_library", query="Paragraph 2", limit=10))
    middle = next(hit for hit in hits if "Paragraph 2" in hit["text"])
    read = payload(call(client, "read_passage", uri=middle["uri"]))
    assert "Paragraph 2" in read["text"]
    assert "Paragraph 1" in (read["before"] or "") or read["after"]
    assert "zqPRIV" not in json.dumps(read)
    assert "shut lease" not in json.dumps(read)


@pytest.mark.parametrize("title", ["Secret lease", "Shut lease"])
def test_a_guessed_uri_opens_nothing(worlds, client, title):
    document_id = document_titled(title)
    with database.read() as connection:
        row = connection.execute(
            sa.select(database.chunks.c.id, database.chunks.c.revision_id).where(
                database.chunks.c.document_id == document_id
            )
        ).one()
    uri = f"securefolio://doc/{document_id}/rev/{row.revision_id}#chunk={row.id}"
    for name, arguments in (
        ("read_passage", {"uri": uri}),
        ("get_document", {"document_id": document_id}),
    ):
        result = call(client, name, **arguments)
        assert result.get("isError")
        assert "zqPRIV" not in json.dumps(result)


def test_a_uri_mixing_an_open_document_with_a_closed_chunk_opens_nothing(
    worlds, client
):
    with database.read() as connection:
        secret_chunk = connection.execute(
            sa.select(database.chunks.c.id).where(
                database.chunks.c.document_id == document_titled("Secret lease")
            )
        ).scalar()
    uri = (
        f"securefolio://doc/{document_titled('Open lease')}/rev/x#chunk={secret_chunk}"
    )
    assert call(client, "read_passage", uri=uri).get("isError")


@pytest.mark.parametrize("change", ["private", "switched_off"])
def test_a_stale_uri_stops_working_at_once(worlds, client, change):
    hits = payload(call(client, "search_library", query="rent bluefin", limit=10))
    uri = hits[0]["uri"]
    document_id = hits[0]["document_id"]
    source_id = worlds["open"]["id"]
    if change == "private":
        sources.set_sensitivity(source_id, states.PRIVATE)
    else:
        sources.set_switches(source_id, mcp_enabled=False)
    assert call(client, "read_passage", uri=uri).get("isError")
    assert call(client, "get_document", document_id=document_id).get("isError")
    assert payload(call(client, "search_library", query="rent bluefin", limit=10)) == []


def test_a_collection_holding_anything_closed_isnt_listed(worlds, client):
    open_id = document_titled("Open lease")
    clear = collections.create("Clear")
    mixed = collections.create("Mixed zqPRIV")
    collections.add_documents(clear["id"], [open_id])
    collections.add_documents(mixed["id"], [open_id, document_titled("Secret lease")])
    listed = payload(call(client, "list_collections"))
    assert [c["name"] for c in listed] == ["Clear"]


def test_a_document_comes_with_a_link(worlds, client):
    open_id = document_titled("Open lease")
    found = payload(call(client, "get_document", document_id=open_id))
    assert found["link"] == f"securefolio://doc/{open_id}"


def test_the_mcp_token_opens_nothing_else(worlds, client):
    assert client.get("/api/settings", headers=HEADERS).status_code in (401, 403)
    assert client.get("/api/library", headers=HEADERS).status_code in (401, 403)


def test_the_app_token_doesnt_open_mcp(worlds, client):
    headers = {**HEADERS, "Authorization": f"Bearer {TEST_TOKENS.app}"}
    answer = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert answer.status_code in (401, 403)


def counts() -> dict[str, int]:
    found = {}
    with database.read() as connection:
        for name, table in database.metadata.tables.items():
            found[name] = connection.execute(
                sa.select(sa.func.count()).select_from(table)
            ).scalar_one()
    return found


def bytes_of_watched(root) -> dict[str, str]:
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_a_whole_session_changes_nothing(worlds, client, tmp_path):
    before_rows, before_files = counts(), bytes_of_watched(tmp_path / "Open")
    objects_before = sorted(p.name for p in vault.objects_dir().rglob("*"))
    hits = payload(call(client, "search_library", query="rent", limit=5))
    payload(call(client, "read_passage", uri=hits[0]["uri"]))
    payload(call(client, "get_document", document_id=hits[0]["document_id"]))
    payload(call(client, "list_collections"))
    assert counts() == before_rows
    assert bytes_of_watched(tmp_path / "Open") == before_files
    assert sorted(p.name for p in vault.objects_dir().rglob("*")) == objects_before


def test_a_copy_in_a_folder_switched_off_isnt_open(worlds, client, tmp_path):
    other = tmp_path / "Other"
    other.mkdir()
    shut_text = (tmp_path / "Open" / "Open lease.txt").read_text()
    (other / "Copy of open lease.txt").write_text(shut_text)
    sources.add_source(str(other), states.STANDARD, mcp_enabled=False)
    drain()
    assert payload(call(client, "search_library", query="rent bluefin", limit=10)) == []


def test_a_folder_closed_during_a_search_takes_its_results_with_it(
    worlds, client, monkeypatch
):
    real = search.retrieve

    def closes(*given):
        found = real(*given)
        sources.set_switches(worlds["open"]["id"], mcp_enabled=False)
        return found

    monkeypatch.setattr(search, "retrieve", closes)
    assert payload(call(client, "search_library", query="rent bluefin", limit=10)) == []


def test_a_document_gone_private_as_its_card_is_read_isnt_returned(
    worlds, client, monkeypatch
):
    open_id = document_titled("Open lease")
    real = cards.get

    def goes_private(document_id):
        found = real(document_id)
        sources.set_sensitivity(worlds["open"]["id"], states.PRIVATE)
        return found

    monkeypatch.setattr(cards, "get", goes_private)
    assert call(client, "get_document", document_id=open_id).get("isError")
