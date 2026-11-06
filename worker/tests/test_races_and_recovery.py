"""The interleavings and terminal outcomes that a straight run never exercises."""

import io
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from reportlab.pdfgen.canvas import Canvas

from agent import models
from backend import (
    database,
    documents,
    embedder,
    index,
    jobs,
    macos,
    objects,
    originals,
    refusals,
    revisions,
    settings,
    sources,
    states,
    threads,
)
from backend.locators import PdfLocator
from data.build import seed
from testing.pdfs import pdf_pages
from testing.queue import drain, next_job
from testing.stub_models import stub_ollama
from worker import (
    chunking,
    embedding,
    hard_pages,
    parse,
    pipeline,
    scan,
    spreadsheets,
    unlock,
)
from worker import main as worker_main
from worker.content import MAX_TOKENS, estimate_tokens, split_to_limit

DATA = Path(__file__).parents[2] / "data" / "library"


@pytest.fixture
def folder(library, tmp_path):
    path = tmp_path / "docs"
    path.mkdir()
    (path / "a.txt").write_text("The quartz clause says rent is due monthly.")
    return path


LOC = PdfLocator(page=1, rects=[], precision="page")


@pytest.fixture
def scanned_pdf(library, tmp_path):
    folder = tmp_path / "pdfs"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(pdf_pages("Plain page of a document."))
    sources.add_source(str(folder), states.STANDARD)
    worker_main.run_job(next_job())
    worker_main.run_job(next_job())


def only_document() -> dict[str, Any]:
    with database.read() as connection:
        return dict(connection.execute(sa.select(database.documents)).mappings().one())


def test_a_folder_made_private_during_a_scan_makes_later_files_private(folder):
    source = sources.add_source(str(folder), states.STANDARD)
    job = next_job()
    sources.set_sensitivity(source["id"], states.PRIVATE)
    scan.run(job)
    jobs.succeed(job["id"])
    assert only_document()["sensitivity"] == states.PRIVATE
    assert (
        documents.locations(only_document()["id"])[0]["sensitivity"] == states.PRIVATE
    )


def test_a_folder_removed_during_a_scan_gets_no_new_rows(folder):
    source = sources.add_source(str(folder), states.STANDARD)
    job = next_job()
    sources.stop_watching(source["id"])
    with pytest.raises(scan.ScanStopped):
        scan.run(job)
    with database.read() as connection:
        assert connection.execute(sa.select(database.documents)).first() is None


def test_chunks_written_after_a_private_move_are_private(folder, monkeypatch):
    source = sources.add_source(str(folder), states.STANDARD)
    worker_main.run_job(next_job())
    job = next_job()
    real = parse.parse

    def move_then_parse(*args):
        sources.set_sensitivity(source["id"], states.PRIVATE)
        return real(*args)

    monkeypatch.setattr(parse, "parse", move_then_parse)
    pipeline.run(job)
    with database.read() as connection:
        level = connection.execute(sa.select(database.chunks.c.sensitivity)).scalar()
    assert level == states.PRIVATE
    assert index.search("quartz", None, index.where_clause([states.STANDARD]), 5) == []
    assert index.search("quartz", None, index.where_clause([states.PRIVATE]), 5)


def test_a_publish_racing_a_private_move_never_leaves_standard_rows(folder):
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    document = only_document()
    results = []

    def republish():
        for _ in range(10):
            index.publish(document["id"], document["active_revision_id"])

    def move():
        sources.set_sensitivity(source["id"], states.PRIVATE)
        results.append(
            index.search("quartz", None, index.where_clause([states.STANDARD]), 5)
        )

    threads = [threading.Thread(target=republish), threading.Thread(target=move)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [[]]
    assert index.search("quartz", None, index.where_clause([states.STANDARD]), 5) == []


def test_a_reindex_is_never_without_passages(folder):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    stop = threading.Event()
    empty = []

    def keep_searching():
        while not stop.is_set():
            if not index.search("quartz", None, None, 5):
                empty.append(1)

    searcher = threading.Thread(target=keep_searching)
    searcher.start()
    document = only_document()
    documents.reindex(document["id"])
    drain()
    stop.set()
    searcher.join()
    assert empty == []
    assert len(index.search("quartz", None, None, 5)) == 1


def test_the_ratchet_covers_a_citation_only_thread_and_its_messages(folder):
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    document_id = only_document()["id"]
    with database.write() as connection:
        connection.execute(
            database.threads.insert().values(
                id="t", title="t", scope={}, mode="standard", created_at=1, updated_at=1
            )
        )
        connection.execute(
            database.messages.insert().values(
                id="m",
                thread_id="t",
                ordinal=0,
                role="assistant",
                content="c",
                answer={"citations": [{"document_id": document_id}]},
                sensitivity=states.STANDARD,
                created_at=1,
            )
        )
    sources.set_sensitivity(source["id"], states.PRIVATE)
    with database.read() as connection:
        assert connection.execute(
            sa.select(database.threads.c.private_tainted)
        ).scalar()
        level = connection.execute(sa.select(database.messages.c.sensitivity)).scalar()
    assert level == states.PRIVATE


def test_cancelling_a_parsed_pdf_discards_its_page_readings(
    library, tmp_path, monkeypatch
):
    path = tmp_path / "docs"
    path.mkdir()
    (path / "a.pdf").write_bytes(pdf_pages("A plain page of text."))
    sources.add_source(str(path), states.STANDARD)
    worker_main.run_job(next_job())
    job = next_job()

    def cancel(_revision):
        documents.cancel_job(job["id"])

    monkeypatch.setattr(embedding, "embed_revision", cancel)
    pipeline.run(job)
    assert jobs.get(job["id"])["state"] == states.CANCELLED
    with database.read() as connection:
        assert connection.execute(sa.select(database.page_readings)).first() is None


def test_an_unexpected_error_fails_the_document_and_cleans_up(folder, monkeypatch):
    sources.add_source(str(folder), states.STANDARD)
    worker_main.run_job(next_job())
    job = next_job()

    def broken(_revision):
        raise RuntimeError("planted")

    monkeypatch.setattr(embedding, "embed_revision", broken)
    pipeline.run(job)
    assert only_document()["status"] == states.FAILED
    assert jobs.get(job["id"])["state"] == states.FAILED
    with database.read() as connection:
        assert connection.execute(sa.select(database.revisions)).first() is None


def test_cancel_just_before_publish_is_a_cancel(folder, monkeypatch):
    sources.add_source(str(folder), states.STANDARD)
    worker_main.run_job(next_job())
    job = next_job()
    real = jobs.advance_stage

    def cancel_after_stage(job_id, stage):
        moved = real(job_id, stage)
        if stage == pipeline.STAGE_PUBLISH:
            documents.cancel_job(job_id)
        return moved

    monkeypatch.setattr(jobs, "advance_stage", cancel_after_stage)
    pipeline.run(job)
    assert jobs.get(job["id"])["state"] == states.CANCELLED
    assert only_document()["status"] == states.CANCELLED


def test_a_kill_after_publish_makes_no_second_revision(folder, monkeypatch):
    sources.add_source(str(folder), states.STANDARD)
    worker_main.run_job(next_job())
    job = next_job()
    pipeline.run(job)
    first = only_document()["active_revision_id"]
    with database.write() as connection:
        connection.execute(
            database.jobs.update()
            .where(database.jobs.c.id == job["id"])
            .values(state=states.RUNNING, stage=pipeline.STAGE_PUBLISH)
        )
    pipeline.run(job)
    assert only_document()["active_revision_id"] == first
    with database.read() as connection:
        assert len(connection.execute(sa.select(database.revisions)).all()) == 1


class TestDownload:
    def placeholder(self, tmp_path) -> Path:
        path = tmp_path / "cloud.txt"
        path.write_text("The cloud file's marker is pepper.")
        macos.use_dataless_stand_in(path)
        return path

    def test_a_watched_placeholder_downloads_and_indexes(
        self, library, tmp_path, monkeypatch
    ):
        folder = tmp_path / "w"
        folder.mkdir()
        path = self.placeholder(folder)
        sources.add_source(str(folder), states.STANDARD)
        drain()
        assert only_document()["status"] == states.NOT_DOWNLOADED
        monkeypatch.setattr(originals, "materialize", macos.clear_dataless_stand_in)
        documents.download_and_index(only_document()["id"])
        drain()
        assert only_document()["status"] == states.INDEXED
        assert not documents.locations(only_document()["id"])[0]["dataless"]
        assert path.exists()

    def test_a_dropped_placeholder_is_copied_in_after_download(
        self, library, tmp_path, monkeypatch
    ):
        path = self.placeholder(tmp_path)
        documents.import_paths([str(path)], states.STANDARD)
        assert only_document()["status"] == states.NOT_DOWNLOADED
        monkeypatch.setattr(originals, "materialize", macos.clear_dataless_stand_in)
        documents.download_and_index(only_document()["id"])
        drain()
        document = only_document()
        assert document["status"] == states.INDEXED
        location = documents.locations(document["id"])[0]
        assert location["storage_mode"] == states.MANAGED
        assert objects.exists(document["content_sha256"])

    def test_a_placeholder_evicted_while_queued_is_listed_not_read(
        self, library, tmp_path, monkeypatch
    ):
        folder = tmp_path / "w"
        folder.mkdir()
        path = folder / "a.txt"
        path.write_text("Soon to be evicted.")
        sources.add_source(str(folder), states.STANDARD)
        worker_main.run_job(next_job())
        macos.use_dataless_stand_in(path)
        monkeypatch.setattr(
            parse, "parse", lambda *_: pytest.fail("read an evicted file")
        )
        drain()
        macos.clear_dataless_stand_in(path)
        assert only_document()["status"] == states.NOT_DOWNLOADED


def test_a_file_edited_after_discovery_publishes_the_bytes_it_read(folder):
    sources.add_source(str(folder), states.STANDARD)
    worker_main.run_job(next_job())
    (folder / "a.txt").write_text("Edited after discovery: the clause is basalt now.")
    drain()
    document = only_document()
    assert document["content_sha256"] == originals.hash_file(folder / "a.txt")
    assert index.search("basalt", None, None, 5)


def test_a_managed_copy_is_stored_under_the_hash_of_its_bytes(tmp_path, library):
    source = tmp_path / "x.txt"
    source.write_text("some bytes")
    sha256, copy = objects.copy_in(source)
    assert originals.hash_file(copy) == sha256
    assert not list(copy.parent.parent.glob("incoming-*"))


def test_a_cancelled_scan_lets_rescan_work(folder):
    source = sources.add_source(str(folder), states.STANDARD)
    job = next_job()
    documents.cancel_job(job["id"])
    with pytest.raises(scan.ScanStopped):
        scan.run(job)
    jobs.finish_cancelled(job["id"])
    sources.settle_scans_with_no_job()
    assert sources.get(source["id"])["status"] == states.WATCHING
    sources.rescan(source["id"])
    drain()
    assert only_document()["status"] == states.INDEXED


def test_a_queued_scan_cancelled_lets_rescan_work(folder):
    source = sources.add_source(str(folder), states.STANDARD)
    with database.read() as connection:
        job_id = connection.execute(sa.select(database.jobs.c.id)).scalar_one()
    documents.cancel_job(job_id)
    sources.rescan(source["id"])
    drain()
    assert only_document()["status"] == states.INDEXED


def test_cancelling_a_hard_pages_job_leaves_nothing_staged(
    library, tmp_path, monkeypatch
):
    path = tmp_path / "docs"
    path.mkdir()
    (path / "a.pdf").write_bytes(pdf_pages("Plain page."))
    sources.add_source(str(path), states.STANDARD)
    worker_main.run_job(next_job())
    worker_main.run_job(next_job())
    job = next_job((jobs.HARD_PAGES,))
    documents.cancel_job(job["id"])
    hard_pages.run(job)
    assert jobs.get(job["id"])["state"] == states.CANCELLED
    with database.read() as connection:
        assert (
            states.STAGING
            not in connection.execute(sa.select(database.revisions.c.state))
            .scalars()
            .all()
        )


def test_spreadsheet_rows_keep_their_own_numbers():
    data = b",,\nItem,Jan\n,\n,\nRent,10\n"
    parsed = spreadsheets.read("CSV", data, "a.csv")
    assert [e.locator.range for e in parsed.elements] == ["A2:B2", "A5:B5"]  # type: ignore[union-attr]


def test_nothing_goes_over_the_token_ceiling():
    long = "word " * 5000
    assert all(estimate_tokens(part) <= MAX_TOKENS for part in split_to_limit(long))
    assert all(
        estimate_tokens(part) <= MAX_TOKENS for part in split_to_limit("字" * 5000)
    )
    parsed = parse.parse("TXT", long.encode(), "a.txt")
    assert all(estimate_tokens(chunk.text) <= MAX_TOKENS for chunk in parsed.chunks)
    sheet = spreadsheets.read("CSV", f"a,b\n{long},1\n".encode(), "a.csv")
    assert all(estimate_tokens(chunk.text) <= MAX_TOKENS for chunk in sheet.chunks)


def test_a_mixed_pdf_stays_in_page_order(library):
    def layout_page(canvas: Canvas) -> None:
        canvas.line(72, 600, 400, 600)
        canvas.drawString(72, 700, "Second page with a ruling line.")

    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(72, 700, "First simple page.")
    canvas.showPage()
    layout_page(canvas)
    canvas.showPage()
    canvas.drawString(72, 700, "Third simple page.")
    canvas.save()
    data = buffer.getvalue()

    def stand_in(_name, _data, page_range):
        from types import SimpleNamespace  # noqa: PLC0415

        from worker import born_digital  # noqa: PLC0415

        return SimpleNamespace(
            document=born_digital.convert(
                data, "x", list(range(page_range[0], page_range[1] + 1))
            ),
            confidence=None,
            pages=[],
        )

    parse.use_recorded(stand_in)
    parsed = parse.parse("PDF", data, "x.pdf")
    assert [e.locator.page for e in parsed.elements] == [1, 2, 3]  # type: ignore[union-attr]


def test_returning_files_become_what_they_are(library, tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    odd = folder / "odd.xyz"
    odd.write_text("x")
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    assert only_document()["status"] == states.SKIPPED_UNSUPPORTED
    odd.unlink()
    sources.rescan(source["id"])
    drain()
    assert only_document()["status"] == states.MISSING
    odd.write_text("x")
    macos.use_dataless_stand_in(odd)
    sources.rescan(source["id"])
    drain()
    macos.clear_dataless_stand_in(odd)
    assert only_document()["status"] == states.NOT_DOWNLOADED


def test_returning_unsupported_file_doesnt_fail_a_parse(library, tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    odd = folder / "odd.xyz"
    odd.write_text("x")
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    odd.unlink()
    sources.rescan(source["id"])
    drain()
    odd.write_text("x")
    sources.rescan(source["id"])
    drain()
    assert only_document()["status"] == states.SKIPPED_UNSUPPORTED


def test_a_password_lives_exactly_as_long_as_the_work_that_needs_it(library):
    job_id = jobs.enqueue(jobs.INGEST, 1, document_id="doc")
    unlock.receive(job_id, "waiting-behind-a-long-queue")
    unlock.receive("no-such-job", "never-claimed")
    unlock.settle(jobs.is_live, jobs.document_has_live_work)
    assert job_id in unlock._box.by_job
    assert "no-such-job" not in unlock._box.by_job
    assert unlock.take_for_job(job_id, "doc", 0) == "waiting-behind-a-long-queue"
    unlock.settle(jobs.is_live, jobs.document_has_live_work)
    assert unlock.held_for("doc")
    next_job()
    jobs.succeed(job_id)
    unlock.settle(jobs.is_live, jobs.document_has_live_work)
    assert unlock.held_for("doc") is None


def test_a_queued_hard_pages_job_cancelled_drops_its_password(library):
    job_id = jobs.enqueue(jobs.HARD_PAGES, 1, document_id="doc")
    unlock.receive("j", "pw")
    unlock.take_for_job("j", "doc", 0)
    documents.cancel_job(job_id)
    unlock.settle(jobs.is_live, jobs.document_has_live_work)
    assert unlock.held_for("doc") is None


def test_ollama_ignores_a_proxy_from_the_environment(library, monkeypatch):
    with stub_ollama() as stub:
        monkeypatch.setenv("OLLAMA_HOST", stub.url)
        monkeypatch.setenv("ALL_PROXY", "http://192.0.2.1:3128")
        monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:3128")
        assert models.local_ready()
        assert stub.requests


def test_offline_turned_on_before_a_download_runs_stops_it(
    library, tmp_path, monkeypatch
):
    folder = tmp_path / "w"
    folder.mkdir()
    path = folder / "cloud.txt"
    path.write_text("Marker pepper.")
    macos.use_dataless_stand_in(path)
    sources.add_source(str(folder), states.STANDARD)
    drain()
    documents.download_and_index(only_document()["id"])
    settings.put(settings.OFFLINE, True)
    asked = []
    monkeypatch.setattr(originals, "materialize", asked.append)
    drain()
    macos.clear_dataless_stand_in(path)
    assert asked == []
    assert only_document()["status"] == states.NOT_DOWNLOADED


def test_a_file_evicted_while_queued_downloads_later(library, tmp_path, monkeypatch):
    folder = tmp_path / "w"
    folder.mkdir()
    path = folder / "a.txt"
    path.write_text("Marker sumac in an evicted file.")
    sources.add_source(str(folder), states.STANDARD)
    worker_main.run_job(next_job())
    macos.use_dataless_stand_in(path)
    drain()
    assert only_document()["status"] == states.NOT_DOWNLOADED
    monkeypatch.setattr(originals, "materialize", macos.clear_dataless_stand_in)
    documents.download_and_index(only_document()["id"])
    drain()
    assert only_document()["status"] == states.INDEXED


def test_an_edit_at_one_of_two_locations_keeps_the_other_content(library, tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    (folder / "a.txt").write_text("Shared content with marker cumin.")
    (folder / "b.txt").write_text("Shared content with marker cumin.")
    sources.add_source(str(folder), states.STANDARD)
    worker_main.run_job(next_job())
    (folder / "a.txt").write_text("Edited: a different marker, nutmeg.")
    drain()
    with database.read() as connection:
        rows = connection.execute(sa.select(database.documents)).mappings().all()
    assert {r["status"] for r in rows} == {states.INDEXED}
    assert index.search("cumin", None, None, 5)


def test_a_new_reindex_file_doesnt_get_read_against_the_old_revision(
    folder, monkeypatch
):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    document = only_document()
    (folder / "a.txt").write_text("Edited after the failed reindex.")
    with database.write() as connection:
        documents.set_content_hash_in(
            connection, document["id"], originals.hash_file(folder / "a.txt")
        )
    with pytest.raises(hard_pages.Stale):
        hard_pages._verified(
            documents.get(document["id"]), document["active_revision_id"]
        )


def test_hard_pages_cancelled_at_publish_is_a_cancel(scanned_pdf, monkeypatch):
    job = next_job((jobs.HARD_PAGES,))
    document = documents.get(job["document_id"])
    real = jobs.advance_stage

    def cancel_after(job_id, stage):
        moved = real(job_id, stage)
        if stage == "publish":
            documents.cancel_job(job_id)
        return moved

    monkeypatch.setattr(jobs, "advance_stage", cancel_after)
    with pytest.raises(hard_pages.HardPagesCancelled):
        hard_pages._publish_changes(
            job,
            document,
            [hard_pages.Element("text", "New.", LOC)],
            document["active_revision_id"],
        )


def test_a_wide_header_cant_hang_the_splitter():
    assert split_to_limit("x y z", 2)
    with pytest.raises(ValueError, match="room"):
        split_to_limit("x", 0)
    wide = " | ".join(f"column{i}" for i in range(3000))
    sheet = spreadsheets.read(
        "CSV", (wide.replace(" | ", ",") + "\n1,2\n").encode(), "a.csv"
    )
    assert all(estimate_tokens(chunk.text) <= MAX_TOKENS for chunk in sheet.chunks)
    table = "| " + wide + " |\n| --- |\n| 1 |"
    pieces = chunking.from_elements([hard_pages.Element("table", table, LOC)])
    assert all(estimate_tokens(chunk.text) <= MAX_TOKENS for chunk in pieces)


def test_ollama_off_the_mac_is_refused_with_no_connection(library, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://192.0.2.1:11434")
    assert (
        not models.local_ready()
    )  # the test's network blocker would fail on an attempt
    with pytest.raises(models.NotLoopback):
        models.read_picture(b"image")


def test_seeding_applies_the_placeholder(library, tmp_path, monkeypatch):
    library_copy = tmp_path / "library"
    shutil.copytree(DATA, library_copy)
    monkeypatch.setattr(seed, "LIBRARY", library_copy)
    seen = []
    monkeypatch.setattr(macos, "use_dataless_stand_in", seen.append)
    monkeypatch.setattr(worker_main, "run_job", lambda job: jobs.succeed(job["id"]))
    seed.main()
    assert any(str(path).endswith("In the cloud.pdf") for path in seen)


def test_refusals_carry_their_status():
    assert refusals.conflict("x").status == 409
    assert refusals.invalid("x").status == 422
    assert embedder.DIMENSION == 1024
    assert revisions.PARSER_VERSION


def test_a_file_overwritten_with_a_private_documents_bytes_joins_it(library, tmp_path):
    secret = tmp_path / "Secret"
    secret.mkdir()
    (secret / "Return.txt").write_text("The secret refund is zq4471.\n")
    sources.add_source(str(secret), states.PRIVATE)
    open_folder = tmp_path / "Open"
    open_folder.mkdir()
    note = open_folder / "Note.txt"
    note.write_text("Nothing much.\n")
    source = sources.add_source(str(open_folder), states.STANDARD)
    drain()
    note.write_text("The secret refund is zq4471.\n")
    sources.rescan(source["id"])
    drain()
    with database.read() as connection:
        rows = (
            connection.execute(
                sa.select(
                    database.documents.c.id,
                    database.documents.c.sensitivity,
                    database.documents.c.content_sha256,
                ).where(database.documents.c.content_sha256.is_not(None))
            )
            .mappings()
            .all()
        )
        location = connection.execute(
            sa.select(database.document_locations.c.document_id).where(
                database.document_locations.c.path == str(note)
            )
        ).scalar_one()
    hashes = [r["content_sha256"] for r in rows if r["id"] == location]
    holders = [r for r in rows if r["content_sha256"] == hashes[0]]
    assert len(holders) == 1
    assert holders[0]["sensitivity"] == states.PRIVATE


def test_an_answer_running_keeps_a_retired_revisions_elements(library, tmp_path):
    (tmp_path / "w").mkdir()
    path = tmp_path / "w" / "Note.txt"
    path.write_text("First words.\n")
    source = sources.add_source(str(tmp_path / "w"), states.STANDARD)
    drain()
    thread = threads.create(None, "standard")["id"]
    run = threads.start_run(thread, "Why?", None)
    with database.read() as connection:
        old = connection.execute(
            sa.select(database.documents.c.active_revision_id)
        ).scalar_one()
    path.write_text("Second words here.\n")
    sources.rescan(source["id"])
    drain()

    def kept() -> int:
        with database.read() as connection:
            return connection.execute(
                sa.select(sa.func.count())
                .select_from(database.elements)
                .where(database.elements.c.revision_id == old)
            ).scalar_one()

    assert kept() > 0
    assert revisions.sweep_retired() == 0
    threads.complete_run(run)
    assert revisions.sweep_retired() > 0
    assert kept() == 0


def test_a_document_an_answer_drew_on_without_citing_taints_its_chat(library, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "Note.txt").write_text("Words.\n")
    source = sources.add_source(str(root), states.STANDARD)
    drain()
    with database.read() as connection:
        document_id = connection.execute(
            sa.select(database.documents.c.id)
        ).scalar_one()
    thread = threads.create(None, "standard")["id"]
    run = threads.start_run(thread, "Why?", None)
    reply = {"role": threads.ROLE_ASSISTANT, "content": "From it, with no citation."}
    assert threads.save_turn(thread, run, reply, [], [document_id])
    threads.complete_run(run)
    assert threads.discussed(thread) == []
    assert not threads.get(thread)["private_tainted"]
    sources.set_sensitivity(source["id"], states.PRIVATE)
    assert threads.get(thread)["private_tainted"]
