"""Search and the list, on real records through the real routes."""

import asyncio
import datetime
import threading
import time

import pytest
import sqlalchemy as sa

from backend import (
    cards,
    collections,
    database,
    documents,
    elements,
    embedder,
    index,
    jobs,
    keychain,
    library,
    macos,
    model_budget,
    notes,
    search,
    sources,
    spend,
    states,
    threads,
    tray,
)
from backend.refusals import Refused
from backend.routes import jobs as job_routes
from testing.queue import drain, next_job
from worker import main as worker_main


@pytest.fixture
def folders(library_vault, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "Lease 3B.txt").write_text(
        "The monthly rent is $2,350.00. Invoice INV-4471 from Maria Okafor."
    )
    (plain / "Roof report.txt").write_text(
        "Two flashing seams need resealing near the chimney."
    )
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "Diagnosis.txt").write_text("The planted marker zanzibar is private.")
    sources.add_source(str(plain), states.STANDARD)
    sources.add_source(str(secret), states.PRIVATE)
    drain()


@pytest.fixture
def library_vault(library):
    return library


def doc_named(title: str) -> dict:
    with database.read() as connection:
        row = (
            connection.execute(
                sa.select(database.documents).where(database.documents.c.title == title)
            )
            .mappings()
            .one()
        )
    return dict(row)


@pytest.mark.parametrize(
    ("query", "title"),
    [("INV-4471", "Lease 3B"), ("$2,350.00", "Lease 3B"), ("Okafor", "Lease 3B")],
)
def test_exact_things_rank_first(folders, query, title):
    results = search.run(query, search.Scope())
    assert results["passages"][0]["document"]["title"] == title
    assert query.casefold() in results["passages"][0]["snippet"].casefold()


def test_typeahead_answers_with_the_model_unloaded(folders):
    calls = []
    original = embedder.embed_query
    embedder.embed_query = lambda text: calls.append(text) or original(text)
    try:
        unloaded = search.run("rent", search.Scope(), ready=lambda: False)
        assert calls == []
        assert unloaded["semantic"] is False
        assert unloaded["passages"]
        merged = search.run("rent", search.Scope(), ready=lambda: True)
        assert calls == ["rent"]
        assert merged["semantic"] is True
        assert merged["passages"]
    finally:
        embedder.embed_query = original


def test_a_prefix_finds_a_document_as_you_type(folders):
    assert search.run("Lea", search.Scope())["documents"][0]["title"] == "Lease 3B"


def test_punctuation_alone_finds_nothing_and_breaks_nothing(folders):
    results = search.run('"$ * (', search.Scope())
    assert results["documents"] == []


class TestPrivate:
    def test_absent_from_the_default_scope_on_every_path(self, folders):
        bm25 = search.run("zanzibar", search.Scope(), ready=lambda: False)
        assert bm25["passages"] == []
        hybrid = search.run("zanzibar", search.Scope(), ready=lambda: True)
        assert "Diagnosis" not in {p["document"]["title"] for p in hybrid["passages"]}
        assert search.run("Diagnosis", search.Scope())["documents"] == []
        assert search.run("Diagnosis", search.Scope())["hidden_private"] == 1
        windows = library.window(
            library.Filters(include_private=False), "date", True, 0, 50
        )
        assert "Diagnosis" not in {row["title"] for row in windows["rows"]}

    def test_a_match_only_in_its_text_is_counted_as_hidden(self, folders):
        """Its title, card and path don't hold the word: only a passage does."""
        for ready in (lambda: False, lambda: True):
            found = search.run("zanzibar", search.Scope(), ready=ready)
            assert found["documents"] == []
            assert found["hidden_private"] == 1
        # Punctuation is read as the index reads it.
        for typed in ("zanzibar?", "Zanzibar,", '"zanzibar"'):
            assert search.run(typed, search.Scope())["hidden_private"] == 1
        # Every word must be in one passage, as a document match needs them all.
        assert search.run("zanzibar lease", search.Scope())["hidden_private"] == 0
        assert (
            search.run("zanzibar", search.Scope(include_private=True))["hidden_private"]
            == 0
        )

    def test_letters_the_index_folds_are_counted_as_it_folds_them(
        self, folders, tmp_path
    ):
        """Ø, Ł and Æ aren't accents NFKD removes; the index folds them anyway."""
        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "Witness.txt").write_text("Signed by Søren Łukasz of Ærø.")
        sources.add_source(str(notes), states.PRIVATE)
        drain()
        for typed in ("Soren", "lukasz", "aero", "Søren Łukasz"):
            assert search.run(typed, search.Scope())["hidden_private"] == 1

    def test_shown_when_the_scope_includes_it(self, folders):
        scope = search.Scope(include_private=True)
        assert search.run("zanzibar", scope)["passages"]
        assert search.run("Diagnosis", scope)["documents"]

    def test_a_stale_index_column_is_dropped_and_refilled(self, folders, monkeypatch):
        """The index still says standard. SQLite is the truth, so the row goes, and
        the refill still finds the other document."""
        leaked = doc_named("Lease 3B")
        with database.write() as connection:
            connection.execute(
                database.documents.update()
                .where(database.documents.c.id == leaked["id"])
                .values(sensitivity=states.PRIVATE)
            )
        fetched = []
        real = index.search

        def recording(text, vector, where, limit):
            fetched.append(limit)
            return real(text, vector, where, limit)

        monkeypatch.setattr(index, "search", recording)
        found = search.run("rent", search.Scope(), ready=lambda: False)
        assert found["passages"] == []
        assert fetched
        titles = search.run("rent", search.Scope(), ready=lambda: True)["passages"]
        assert leaked["id"] not in {p["document"]["id"] for p in titles}
        assert {p["document"]["title"] for p in titles} == {"Roof report"}
        # SQLite, not the stale column, says it's Private, so the note still counts it.
        for typed in ("monthly", "INV-4471"):
            assert search.run(typed, search.Scope())["hidden_private"] == 1

    def test_a_retired_revision_is_never_shown(self, folders):
        document = doc_named("Lease 3B")
        with database.write() as connection:
            connection.execute(
                database.documents.update()
                .where(database.documents.c.id == document["id"])
                .values(active_revision_id="retired")
            )
        results = search.run("rent", search.Scope(), ready=lambda: True)["passages"]
        assert document["id"] not in {p["document"]["id"] for p in results}

    def test_every_file_route_still_serves_private_to_the_owner(
        self, folders, app_client
    ):
        document = doc_named("Diagnosis")
        assert app_client.get(f"/api/documents/{document['id']}").status_code == 200


class TestScopes:
    def test_a_source_filter_applies_before_scoring(self, folders):
        document = doc_named("Roof report")
        source_id = documents.locations(document["id"])[0]["source_id"]
        scope = search.Scope(source_id=source_id)
        assert search.run("Roof", scope)["documents"]
        others = [s for s in sources.live() if s["id"] != source_id]
        assert not search.run("Roof", search.Scope(source_id=others[0]["id"]))[
            "documents"
        ]

    def test_collection_type_date_and_tag(self, folders):
        document = doc_named("Lease 3B")
        made = collections.create("Home")
        collections.add_documents(made["id"], [document["id"]])
        scope = search.Scope(collection_id=made["id"])
        assert search.run("rent", scope)["passages"]
        assert {
            p["document"]["id"] for p in search.run("flashing", scope)["passages"]
        } == {document["id"]}
        cards.edit_card(document["id"], {"tags": ["home"], "doc_type": "Contract"})
        assert search.run("rent", search.Scope(tag="home"))["passages"]
        assert not search.run("rent", search.Scope(tag="other"))["passages"]
        assert search.run("rent", search.Scope(doc_type="Contract"))["documents"] == []
        assert search.run("Lease", search.Scope(doc_type="Contract"))["documents"]
        assert not search.run("Lease", search.Scope(date_from="2999-01-01"))[
            "documents"
        ]


class TestLibrary:
    def test_window_sort_and_total(self, folders, app_client):
        window = app_client.get(
            "/api/library", params={"sort": "title", "descending": False, "limit": 2}
        ).json()
        assert window["total"] == 3
        assert [row["title"] for row in window["rows"]] == ["Diagnosis", "Lease 3B"]
        rest = app_client.get(
            "/api/library",
            params={"sort": "title", "descending": False, "offset": 2},
        ).json()
        assert [row["title"] for row in rest["rows"]] == ["Roof report"]

    def test_select_all_matches_the_filters(self, folders, app_client):
        everything = app_client.get("/api/library/ids").json()
        assert len(everything) == 3
        named = app_client.get("/api/library/ids", params={"name": "roof"}).json()
        assert named == [doc_named("Roof report")["id"]]

    def test_a_percent_in_the_name_filter_is_literal(self, folders, app_client):
        assert app_client.get("/api/library/ids", params={"name": "%"}).json() == []

    def test_recents_and_starred_are_stored(self, folders, app_client):
        first, second = doc_named("Lease 3B"), doc_named("Roof report")
        app_client.post(f"/api/documents/{first['id']}/opened")
        time.sleep(0.01)
        app_client.post(f"/api/documents/{second['id']}/opened")
        recents = app_client.get("/api/library", params={"view": "recents"}).json()
        assert [r["title"] for r in recents["rows"]] == ["Roof report", "Lease 3B"]
        app_client.put(f"/api/documents/{first['id']}/star", json={"starred": True})
        starred = app_client.get("/api/library", params={"view": "starred"}).json()
        assert [r["title"] for r in starred["rows"]] == ["Lease 3B"]

    def test_facets_and_counts(self, folders, app_client):
        document = doc_named("Lease 3B")
        cards.edit_card(document["id"], {"tags": ["home"], "doc_type": "Contract"})
        facets = app_client.get("/api/library/facets").json()
        assert {"value": "home", "count": 1} in facets["tags"]
        assert {"value": "Contract", "count": 1} in facets["types"]
        assert {f["label"] for f in facets["folders"]} == {"plain", "secret"}
        counts = app_client.get("/api/library/counts").json()
        assert counts["total"] == 3

    def test_a_removed_document_leaves_the_list(self, folders, app_client):
        document = doc_named("Roof report")
        app_client.post(
            "/api/documents/remove", json={"document_ids": [document["id"]]}
        )
        assert app_client.get("/api/library").json()["total"] == 2
        left = search.run("flashing", search.Scope())["passages"]
        assert document["id"] not in {p["document"]["id"] for p in left}


class TestNotesAndCollections:
    def test_a_note_carries_its_documents_sensitivity_and_follows_it(self, folders):
        document = doc_named("Lease 3B")
        note = notes.create("Check the renewal.", document["id"])
        assert note["sensitivity"] == states.STANDARD
        source_id = documents.locations(document["id"])[0]["source_id"]
        sources.set_sensitivity(source_id, states.PRIVATE)
        found = notes.all_notes(document["id"])
        assert found[0]["sensitivity"] == states.PRIVATE

    def test_a_citation_opens_after_the_document_is_indexed_again(
        self, folders, app_client, tmp_path
    ):
        document = doc_named("Lease 3B")
        text = app_client.get(f"/api/documents/{document['id']}/text").json()
        element = text["elements"][0]
        citation = {
            "document_id": document["id"],
            "revision_id": document["active_revision_id"],
            "element_id": element["id"],
        }
        with database.write() as connection:
            connection.execute(
                database.notes.insert().values(
                    id="n",
                    title="t",
                    body="b",
                    citations=[citation],
                    source_label="Saved from a chat",
                    sensitivity=states.STANDARD,
                    created_at=time.time(),
                )
            )
        opened = app_client.get("/api/notes").json()[0]["citations"][0]["opens"]
        assert opened["state"] == "current"
        documents.reindex(document["id"])
        drain()
        opened = app_client.get("/api/notes").json()[0]["citations"][0]["opens"]
        assert opened["state"] == "moved"
        assert opened["element"]["text"] == element["text"]

    def test_collections_add_and_remove(self, folders, app_client):
        made = app_client.post("/api/collections", json={"name": "Home"}).json()
        document = doc_named("Lease 3B")
        body = {"document_ids": [document["id"]]}
        app_client.post(f"/api/collections/{made['id']}/add", json=body)
        listed = app_client.get("/api/collections").json()
        assert listed[0]["count"] == 1
        assert listed[0]["titles"] == ["Lease 3B"]
        app_client.post(f"/api/collections/{made['id']}/remove", json=body)
        assert app_client.get("/api/collections").json()[0]["count"] == 0
        assert (
            app_client.post("/api/collections", json={"name": " "}).status_code == 422
        )


class TestTray:
    def test_rows_counts_and_a_stream_of_changes(
        self, library_vault, tmp_path, app_client
    ):
        folder = tmp_path / "w"
        folder.mkdir()
        (folder / "a.txt").write_text("Text.")
        (folder / "b.bin").write_text("Not a format we read.")
        sources.add_source(str(folder), states.STANDARD)
        drain()
        counts = app_client.get("/api/jobs/counts").json()
        assert counts["completed"] == 1
        completed = app_client.get("/api/jobs", params={"tab": "completed"}).json()
        assert completed[0]["file"] == "a.txt"
        assert completed[0]["folder"] == "w"

        async def first_event() -> str:
            async for event in job_routes.counts_events(rounds=1):
                return event
            return ""

        assert '"completed": 1' in asyncio.run(first_event())

    def test_skipped_lists_documents_a_scan_marked_with_no_job(
        self, library_vault, tmp_path, app_client
    ):
        folder = tmp_path / "w"
        folder.mkdir()
        path = folder / "cloud.pdf"
        path.write_bytes(b"%PDF-1.7")
        macos.use_dataless_stand_in(path)
        sources.add_source(str(folder), states.STANDARD)
        drain()
        rows = app_client.get("/api/jobs", params={"tab": "skipped"}).json()
        assert [r["file"] for r in rows] == ["cloud.pdf"]
        assert rows[0]["skip_kind"] == "cloud"
        assert rows[0]["job_id"] is None
        macos.clear_dataless_stand_in(path)

    def test_retry_and_cancel_reach_the_job(self, library_vault, tmp_path, app_client):
        folder = tmp_path / "w"
        folder.mkdir()
        (folder / "a.txt").write_text("Text.")
        sources.add_source(str(folder), states.STANDARD)
        worker_main.run_job(next_job())
        active = app_client.get("/api/jobs", params={"tab": "active"}).json()
        assert active[0]["status"] == "Queued"
        app_client.post(f"/api/jobs/{active[0]['job_id']}/cancel")
        assert app_client.get("/api/jobs", params={"tab": "active"}).json() == []


def test_the_ask_route_comes_from_the_backend(app_client, monkeypatch):
    monkeypatch.setattr(keychain, "get_key", lambda *_given: "test-not-a-real-key")
    route = app_client.get("/api/ask/route").json()
    assert route["local"] is False
    assert route["model"] == "anthropic:claude-sonnet-5-5"
    assert route["name"] == "Claude Sonnet 5.5"
    assert route["provider"] == "anthropic"
    assert route["blocked"] is None
    assert app_client.get("/api/ask/route", params={"mode": "deep"}).json()["name"] == (
        "Claude Opus 5.5"
    )
    private = app_client.get("/api/ask/route", params={"include_private": True}).json()
    assert private["local"] is True


def test_the_month_starts_on_the_first():
    moment = spend.month_start(datetime.date(2026, 11, 15))
    assert datetime.datetime.fromtimestamp(moment, datetime.UTC).day == 1


class TestScopeBeforeTheCutoff:
    @pytest.fixture
    def crowded(self, library_vault):
        """More documents than are ever fetched, all matching, and one more that
        matches only by its path, so it ranks last."""
        now = time.time()
        with database.write() as connection:
            for number in range(search.DOCUMENTS_FETCHED + 20):
                level = states.PRIVATE if number % 2 else states.STANDARD
                make_document(connection, f"c{number}", "Common word", level)
            make_document(
                connection, "low", "Other name", states.STANDARD, path="/common"
            )
            connection.execute(
                database.collections.insert().values(
                    id="only", name="Only", created_at=now, updated_at=now
                )
            )
            connection.execute(
                database.collection_items.insert().values(
                    collection_id="only", document_id="low"
                )
            )

    def test_a_scoped_match_below_the_cutoff_is_found(self, crowded):
        found = search.run("common", search.Scope(collection_id="only"))
        assert [row["id"] for row in found["documents"]] == ["low"]

    def test_hidden_private_counts_only_what_the_scope_covers(self, crowded):
        assert search.run("common", search.Scope())["hidden_private"] > 0
        scoped = search.run("common", search.Scope(collection_id="only"))
        assert scoped["hidden_private"] == 0


def make_document(connection, doc_id, title, level, path="/x"):
    now = time.time()
    connection.execute(
        database.documents.insert().values(
            id=doc_id,
            title=title,
            file_name=f"{title}.pdf",
            file_kind="PDF",
            status=states.INDEXED,
            sensitivity=level,
            added_at=now,
        )
    )
    connection.exec_driver_sql(
        "INSERT INTO documents_fts (document_id, title, file_name, path, summary, "
        "entities) VALUES (?, ?, ?, ?, '', '')",
        (doc_id, title, f"{title}.pdf", path),
    )


def test_a_note_written_as_its_document_turns_private_comes_out_private(folders):
    document = doc_named("Lease 3B")
    source_id = documents.locations(document["id"])[0]["source_id"]
    made: list[dict] = []
    started = threading.Event()

    def write_note() -> None:
        started.set()
        made.append(notes.create("Written meanwhile.", document["id"]))

    thread = threading.Thread(target=write_note)
    with database.write() as connection:
        connection.execute(
            database.documents.update()
            .where(database.documents.c.id == document["id"])
            .values(sensitivity=states.PRIVATE)
        )
        thread.start()
        started.wait()
        time.sleep(0.1)  # the note's transaction is now waiting on this one
    thread.join()
    assert made[0]["sensitivity"] == states.PRIVATE
    assert source_id


def test_a_note_on_no_such_document_is_refused(library_vault):
    with pytest.raises(Refused):
        notes.create("Text.", "nope")


class TestCitationAfterAnEdit:
    def cite_first(self, folder):
        (folder / "Repeats.txt").write_text(
            "Same words.\n\nOther words.\n\nSame words.\n"
        )
        sources.add_source(str(folder), states.STANDARD)
        drain()
        document = doc_named("Repeats")
        element = elements.of_document(document["id"], 0, 10)["elements"][0]
        citation = {
            "document_id": document["id"],
            "revision_id": document["active_revision_id"],
            "element_id": element["id"],
        }
        with database.write() as connection:
            connection.execute(
                database.notes.insert().values(
                    id="n",
                    title="t",
                    body="b",
                    citations=[citation],
                    source_label="Saved from a chat",
                    sensitivity=states.STANDARD,
                    created_at=time.time(),
                )
            )
        return document, element

    def test_repeated_text_doesnt_move_to_another_occurrence(
        self, library_vault, tmp_path
    ):
        document, element = self.cite_first(tmp_path)
        (tmp_path / "Repeats.txt").write_text(
            "New opening.\n\nSame words.\n\nOther words.\n\nSame words.\n"
        )
        worker_scan_and_drain(document["id"])
        opened = elements.resolve(
            document["id"], document["active_revision_id"], element["id"]
        )
        assert opened["state"] == "stored"

    def test_the_same_place_still_resolves(self, library_vault, tmp_path):
        document, element = self.cite_first(tmp_path)
        (tmp_path / "Repeats.txt").write_text(
            "Same words.\n\nChanged words.\n\nSame words.\n"
        )
        worker_scan_and_drain(document["id"])
        opened = elements.resolve(
            document["id"], document["active_revision_id"], element["id"]
        )
        assert opened["state"] == "moved"
        assert opened["element"]["locator"] == element["locator"]


def worker_scan_and_drain(document_id: str) -> None:
    source_id = documents.locations(document_id)[0]["source_id"]
    sources.rescan(source_id)
    drain()


class TestTrayReachesEverything:
    def test_counts_are_whole_and_rows_grow_on_request(self, library_vault, tmp_path):
        folder = tmp_path / "w"
        folder.mkdir()
        for number in range(tray.LIMIT + 5):
            (folder / f"n{number}.txt").write_text(f"Note {number}.")
        sources.add_source(str(folder), states.STANDARD)
        drain()
        assert tray.counts()["completed"] == tray.LIMIT + 5
        assert len(tray.rows("completed")) == tray.LIMIT
        assert len(tray.rows("completed", tray.LIMIT + 5)) == tray.LIMIT + 5

    def test_a_reindex_waiting_for_a_password_can_be_unlocked(
        self, library_vault, tmp_path, app_client
    ):
        folder = tmp_path / "w"
        folder.mkdir()
        (folder / "a.txt").write_text("Text.")
        sources.add_source(str(folder), states.STANDARD)
        drain()
        document = doc_named("a")
        job_id = documents.reindex(document["id"])
        next_job()
        jobs.skip(job_id, "This file is password-protected.", "password")
        row = app_client.get("/api/jobs", params={"tab": "skipped"}).json()[0]
        assert row["document_id"] == document["id"]
        assert row["skip_kind"] == "password"
        assert app_client.get("/api/jobs/counts").json()["skipped"] == 1

    def test_a_retry_clears_an_old_failure(self, library_vault, tmp_path):
        folder = tmp_path / "w"
        folder.mkdir()
        (folder / "a.txt").write_text("Text.")
        sources.add_source(str(folder), states.STANDARD)
        drain()
        document = doc_named("a")
        job_id = documents.reindex(document["id"])
        next_job()
        jobs.fail(job_id, "No.", "internal")
        assert tray.counts()["failed"] == 1
        documents.reindex(document["id"])
        assert tray.counts()["failed"] == 0


def test_rows_past_any_fixed_cap_can_be_asked_for(library_vault, app_client):
    assert app_client.get("/api/jobs", params={"limit": 5100}).status_code == 200


def test_a_document_is_read_after_its_work_is_asked_about(
    folders, app_client, monkeypatch
):
    """Read the other way round, a job finishing in between would report the old
    revision as final, and the viewer would stop following."""
    document = doc_named("Lease 3B")
    order: list[str] = []
    real_live, real_get = jobs.document_has_live_work, documents.get

    def live(document_id):
        order.append("live")
        return real_live(document_id)

    def get(document_id):
        order.append("document")
        return real_get(document_id)

    monkeypatch.setattr(jobs, "document_has_live_work", live)
    monkeypatch.setattr(documents, "get", get)
    app_client.get(f"/api/documents/{document['id']}")
    assert order[:2] == ["live", "document"]


@pytest.mark.parametrize(
    ("value", "iso"),
    [
        ("2025-03-06", "2025-03-06"),
        ("2025-03", "2025-03-01"),
        ("2025", "2025-01-01"),
        ("March 6, 2029", "2029-03-06"),
        ("March 2029", "2029-03-01"),
        ("soon", None),
    ],
)
def test_a_cards_date_is_read(value, iso):
    assert cards.iso_date(value) == iso


def test_a_cards_type_and_date_reach_the_list_and_the_index(folders):
    document = doc_named("Lease 3B")
    cards.edit_card(
        document["id"],
        {"doc_type": "Contract", "dates": [{"label": "Year", "value": "2025"}]},
    )
    assert doc_named("Lease 3B")["doc_date"] == "2025-01-01"
    found = index.search("rent", None, index.where_clause(doc_type="Contract"), 5)
    assert found
    assert not index.search("rent", None, index.where_clause(date_from="2026-01-01"), 5)


def test_a_cards_words_reach_the_documents_search(library, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "Scan 0041.txt").write_text("Words.\n")
    sources.add_source(str(root), states.STANDARD)
    drain()
    with database.read() as connection:
        row = connection.execute(sa.select(database.documents)).mappings().one()
    cards.write_card(
        row["id"],
        row["active_revision_id"],
        {
            "title": "Zebrafish contract",
            "summary": "About quokkas.",
            "people": ["Ngaio"],
        },
        "Test",
    )
    scope = search.Scope()
    for word in ("Zebrafish", "quokkas", "Ngaio"):
        found = search.run(word, scope, ready=lambda: False)["documents"]
        assert [d["id"] for d in found] == [row["id"]], word


def test_the_reranker_flag_doesnt_outlive_its_process(library, app_client):
    assert model_budget.reranker_loaded() is False


def test_a_citation_from_before_a_reindex_opens_at_the_same_words(library, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    path = root / "Note.txt"
    path.write_text("Words that stay.\n\nWords that go.\n")
    source = sources.add_source(str(root), states.STANDARD)
    drain()
    with database.read() as connection:
        row = connection.execute(sa.select(database.documents)).mappings().one()
        old = (
            connection.execute(
                sa.select(database.elements).where(
                    database.elements.c.revision_id == row["active_revision_id"],
                    database.elements.c.text.like("%stay%"),
                )
            )
            .mappings()
            .one()
        )
        thread = threads.create(None, "standard")["id"]
    run = threads.start_run(thread, "Why?", None)
    reply = {
        "role": threads.ROLE_ASSISTANT,
        "content": "It stays.",
        "answer": {
            "citations": [
                {
                    "document_id": row["id"],
                    "revision_id": row["active_revision_id"],
                    "element_id": old["id"],
                }
            ]
        },
    }
    threads.save_turn(thread, run, reply, [row["id"]])
    threads.complete_run(run)
    path.write_text("Words that stay.\n\nNew words.\n")
    sources.rescan(source["id"])
    drain()
    found = elements.cited(row["id"], old["id"])
    assert found["current"] is True
    assert found["element"]["id"] != old["id"]
    assert "stay" in found["element"]["text"]
