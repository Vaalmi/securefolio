"""Moves, copies, changes and deletions, by a scan alone and with no re-parse."""

import ast
import time
from pathlib import Path

import pytest

from backend import (
    database,
    documents,
    embedder,
    jobs,
    originals,
    sources,
    states,
    vault,
)
from backend.database import document_locations
from testing.counts import Counts
from testing.queue import drain, next_job
from worker import main as worker_main
from worker import parse, scan, watcher


@pytest.fixture
def counted(library, monkeypatch):
    """Counts every parse and embed, so a test can show there were none."""
    counts = Counts()
    monkeypatch.setattr(parse, "parse", counts.wrap_parse(parse.parse))
    monkeypatch.setattr(
        embedder, "embed_documents", counts.wrap_embed(embedder.embed_documents)
    )
    return counts


def folder_with(tmp_path, name="watched", files=None):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    for file_name, text in (
        files or {"note.txt": "A plain note about the roof."}
    ).items():
        (folder / file_name).write_text(text)
    return folder


def only_document():
    with database.read() as connection:
        return dict(connection.execute(database.documents.select()).mappings().one())


def rescan(source):
    sources.rescan(source["id"], by_owner=True)
    drain()


def test_a_rename_keeps_the_document_with_no_reparse_or_reembed(tmp_path, counted):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    before = only_document()
    counted.reset()
    (folder / "note.txt").rename(folder / "renamed.txt")
    rescan(source)
    after = only_document()
    assert after["id"] == before["id"]
    assert after["active_revision_id"] == before["active_revision_id"]
    assert (counted.parses, counted.embeds) == (0, 0)
    assert documents.locations(after["id"])[0]["path"].endswith("renamed.txt")
    assert after["status"] == states.INDEXED


def test_a_copy_with_the_old_path_still_there_is_a_second_location(tmp_path, counted):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    counted.reset()
    (folder / "copy.txt").write_text((folder / "note.txt").read_text())
    rescan(source)
    assert len(documents.locations(only_document()["id"])) == 2
    assert (counted.parses, counted.embeds) == (0, 0)


def test_a_deleted_file_is_missing_not_purged(tmp_path, counted):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    (folder / "note.txt").unlink()
    rescan(source)
    document = only_document()
    assert document["status"] == states.MISSING
    assert document["active_revision_id"]


def test_a_missing_file_coming_back_is_indexed_with_nothing_reparsed(tmp_path, counted):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    text = (folder / "note.txt").read_text()
    (folder / "note.txt").unlink()
    rescan(source)
    counted.reset()
    (folder / "moved.txt").write_text(text)
    rescan(source)
    assert only_document()["status"] == states.INDEXED
    assert counted.parses == 0


def test_relocate_checks_the_hash(tmp_path, counted):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    text = (folder / "note.txt").read_text()
    (folder / "note.txt").unlink()
    rescan(source)
    document_id = only_document()["id"]
    other = tmp_path / "other"
    other.mkdir()
    (other / "wrong.txt").write_text("different")
    with pytest.raises(Exception, match="different"):
        documents.relocate(document_id, str(other / "wrong.txt"), False)
    (other / "right.txt").write_text(text)
    documents.relocate(document_id, str(other / "right.txt"), False)
    assert only_document()["status"] == states.INDEXED


def test_relocating_to_a_new_version_when_confirmed_queues_a_reindex(tmp_path, counted):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    (folder / "note.txt").unlink()
    rescan(source)
    new = tmp_path / "new.txt"
    new.write_text("A new version of the note.")
    documents.relocate(only_document()["id"], str(new), True)
    counted.reset()
    drain()
    assert counted.parses == 1
    assert only_document()["status"] == states.INDEXED


def test_a_failed_document_that_goes_missing_comes_back_as_a_new_file(
    tmp_path, library, monkeypatch
):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)

    def broken(*_args):
        raise parse.CantRead("No.")

    with monkeypatch.context() as patched:
        patched.setattr(parse, "parse", broken)
        drain()
    assert only_document()["status"] == states.FAILED
    text = (folder / "note.txt").read_text()
    (folder / "note.txt").unlink()
    rescan(source)
    assert only_document()["status"] == states.MISSING
    (folder / "note.txt").write_text(text)
    rescan(source)
    assert only_document()["status"] == states.INDEXED
    assert only_document()["active_revision_id"]


def test_a_changed_file_is_indexed_again_and_stays_searchable_meanwhile(
    tmp_path, counted
):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    first = only_document()
    (folder / "note.txt").write_text("Now the note is about the basement instead.")
    sources.rescan(source["id"])
    job = next_job()
    while job["kind"] != jobs.SCAN:
        job = next_job()
    scan.run(job)
    jobs.succeed(job["id"])
    assert only_document()["status"] == states.INDEXED
    assert only_document()["active_revision_id"] == first["active_revision_id"]
    drain()
    assert only_document()["active_revision_id"] != first["active_revision_id"]


def test_a_removed_document_isnt_added_back_unless_a_new_folder_holds_it(
    tmp_path, library
):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    document_id = only_document()["id"]
    documents.remove_from_library([document_id])
    assert (folder / "note.txt").exists()
    (folder / "again.txt").write_text((folder / "note.txt").read_text())
    rescan(source)
    assert only_document()["status"] == states.REMOVED
    other = folder_with(
        tmp_path, "other", {"same.txt": (folder / "note.txt").read_text()}
    )
    sources.add_source(str(other), states.STANDARD)
    drain()
    assert only_document()["status"] == states.INDEXED


def test_stopping_watching_leaves_the_files_and_removes_documents_with_nowhere_else(
    tmp_path, library
):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    before = (folder / "note.txt").read_bytes()
    sources.stop_watching(source["id"])
    assert only_document()["status"] == states.REMOVED
    assert (folder / "note.txt").read_bytes() == before
    assert sources.get(source["id"])["status"] == states.REMOVED


def test_the_built_in_source_cant_be_stopped(library):
    with pytest.raises(Exception, match="stays"):
        sources.stop_watching(states.LIBRARY_SOURCE_ID)


def test_a_folder_inside_the_vault_or_already_watched_is_refused(tmp_path, library):
    with pytest.raises(Exception, match="own library"):
        sources.add_source(str(vault.root()), states.STANDARD)
    folder = folder_with(tmp_path)
    sources.add_source(str(folder), states.STANDARD)
    with pytest.raises(Exception, match="already watched"):
        sources.add_source(str(folder), states.STANDARD)
    with pytest.raises(Exception, match="isn't a folder"):
        sources.add_source(str(folder / "note.txt"), states.STANDARD)


def test_a_scan_job_that_fails_leaves_the_folder_needing_attention(tmp_path, library):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    (folder / "note.txt").unlink()
    folder.rmdir()
    drain()
    after = sources.get(source["id"])
    assert after["status"] == states.NEEDS_ATTENTION
    assert after["status_reason"]
    folder.mkdir()
    (folder / "note.txt").write_text("Back again.")
    rescan(source)
    assert sources.get(source["id"])["status"] == states.WATCHING


def test_attempts_running_out_also_leave_a_reason(tmp_path, library):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    for _ in range(jobs.MAX_ATTEMPTS):
        next_job()
        with database.write() as connection:
            connection.execute(
                database.jobs.update().values(lease_expires_at=time.time() - 1)
            )
        worker_main.requeue_expired()
    assert sources.get(source["id"])["status"] == states.NEEDS_ATTENTION


def test_rescan_on_a_paused_folder_scans_once_and_stays_paused(tmp_path, library):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    sources.pause(source["id"])
    (folder / "new.txt").write_text("A new file while paused.")
    sources.rescan(source["id"], by_owner=True)
    job = next_job()
    assert job["kind"] == jobs.SCAN
    scan.run(job)
    jobs.succeed(job["id"])
    assert sources.get(source["id"])["status"] == states.PAUSED
    assert jobs.claim() is None  # its file's job waits for Resume
    sources.resume(source["id"])
    drain()
    assert sources.get(source["id"])["status"] == states.WATCHING
    with database.read() as connection:
        assert len(connection.execute(database.documents.select()).all()) == 2


def test_the_timer_doesnt_scan_a_paused_folder(tmp_path, library):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    sources.pause(source["id"])
    with pytest.raises(Exception, match="paused"):
        sources.rescan(source["id"], by_owner=False)
    scan.enqueue_timer_scans()
    assert jobs.claim() is None


def test_a_folder_paused_or_removed_during_a_scan_keeps_that_state(tmp_path, library):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    job = next_job()
    sources.pause(source["id"])
    with pytest.raises(scan.ScanStopped):
        scan.run(job)
    sources.finish_scan(source["id"], False)
    sources.fail_scan(source["id"], "x")
    assert sources.get(source["id"])["status"] == states.PAUSED


def test_ignored_files_arent_seen(tmp_path, library):
    folder = folder_with(
        tmp_path, files={"~$lock.docx": "x", ".DS_Store": "x", "note.txt": "text here"}
    )
    sources.add_source(str(folder), states.STANDARD)
    drain()
    assert only_document()["file_name"] == "note.txt"


def test_an_event_is_only_a_hint_for_those_paths(tmp_path, library):
    folder = folder_with(tmp_path)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    new = folder / "event.txt"
    new.write_text("A file an event told us about.")
    watcher.hint([(1, str(new))], [source])
    drain()
    with database.read() as connection:
        rows = connection.execute(document_locations.select()).mappings().all()
    assert {r["path"] for r in rows} == {str(new), str(folder / "note.txt")}
    new.unlink()
    watcher.hint([(3, str(new))], [source])
    drain()
    gone = documents.locations(
        next(r["document_id"] for r in rows if r["path"] == str(new))
    )
    assert gone[0]["missing"]


def test_nothing_but_originals_opens_or_changes_a_users_path():
    """An AST check over everything that handles a location's path: it may only go
    through originals.py."""
    root = Path(__file__).parents[2]
    handlers = (
        "backend/documents.py",
        "backend/sources.py",
        "worker/scan.py",
        "worker/locations.py",
        "worker/pipeline.py",
        "worker/hard_pages.py",
    )
    calls = {"open", "read_bytes", "read_text", "write_bytes", "write_text", "unlink"}
    calls |= {"rename", "rmtree", "remove", "chmod", "touch"}
    for relative in handlers:
        for node in ast.walk(ast.parse((root / relative).read_text())):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                owner = getattr(getattr(node.func, "value", None), "id", None)
                if owner in ("originals", "Image") or isinstance(
                    getattr(node.func, "value", None), ast.Constant
                ):
                    continue
                assert name not in calls, (relative, name, node.lineno)


def test_originals_opens_read_only(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("x")
    path.chmod(0o400)
    assert originals.read_bytes(path) == b"x"
    assert originals.hash_file(path)
