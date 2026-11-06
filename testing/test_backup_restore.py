"""A restore on a clean vault brings the library back, with nothing parsed, embedded or
read from a watched file, and no deleted memory returning."""

import asyncio
import builtins
import contextvars
import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa

from backend import (
    backup,
    cards,
    database,
    dependencies,
    documents,
    egress,
    embedder,
    memories,
    notes,
    refusals,
    search,
    settings,
    sources,
    states,
    threads,
    vault,
)
from backend.migrations import run as migrations
from testing.queue import drain
from worker import parse


@pytest.fixture
def filled(library, tmp_path):
    root = tmp_path / "watched"
    root.mkdir()
    (root / "Lease.txt").write_text("The rent is due monthly.\n")
    sources.add_source(str(root), states.STANDARD)
    drain()
    return root


def count_of(table) -> int:
    with database.read() as connection:
        return connection.execute(
            sa.select(sa.func.count()).select_from(table)
        ).scalar_one()


def document_id() -> str:
    with database.read() as connection:
        return connection.execute(sa.select(database.documents.c.id)).scalar_one()


def add_memory(text: str) -> str:
    return memories.add(
        {
            "kind": "fact",
            "text": text,
            "embedding": None,
            "sensitivity": states.STANDARD,
            "source_thread_id": None,
            "source_label": "A chat",
        }
    )


def test_a_backup_holds_the_library_and_a_manifest(filled):
    made = backup.back_up()
    home = backup.folder() / made["id"]
    assert sqlite3.connect(home / backup.LIBRARY).execute(
        "SELECT count(*) FROM documents"
    ).fetchone() == (1,)
    manifest = json.loads((home / backup.MANIFEST).read_text())
    assert manifest["watched"][0]["path"].endswith("Lease.txt")
    assert not (home / "index").exists()


def test_only_the_last_seven_are_kept(filled):
    start = time.time()
    for day in range(9):
        backup.back_up(start + day * 86400)
    found = backup.list_backups()
    assert len(found) == backup.KEEP
    assert found[0]["created_at"] == start + 8 * 86400


def test_run_if_due_waits_a_day_and_honours_the_switch(filled):
    now = time.time()
    assert backup.run_if_due(now) is True
    assert backup.run_if_due(now + 3600) is False
    assert backup.run_if_due(now + 86401) is True
    settings.put(settings.BACKUP_NIGHTLY, False)
    assert backup.run_if_due(now + 200000) is False


def test_a_failure_shows_where_settings_can_read_it(filled, monkeypatch):
    def fails(_moment):
        raise OSError("the disk is full")

    monkeypatch.setattr(backup, "back_up", fails)
    last = backup.run_now()
    assert "the disk is full" in last["error"]
    assert settings.get(settings.BACKUP_LAST)["error"] == last["error"]


def test_the_chosen_folder_is_used(filled, tmp_path):
    settings.put(settings.BACKUP_FOLDER, str(tmp_path / "elsewhere"))
    backup.back_up()
    assert list((tmp_path / "elsewhere").iterdir())


def test_a_missing_backup_is_refused(filled):
    with pytest.raises(refusals.Refused):
        backup.restore("../../nothing")


class TestRestore:
    def setup_it(self, filled) -> str:
        note = notes.create("A note kept.", document_id())
        thread = threads.create(None, "standard")
        threads.start_run(thread["id"], "A question", None)
        cards.edit_card(document_id(), {"summary": "Corrected by the owner."})
        made = backup.back_up()
        assert note
        assert made
        return made["id"]

    def clean_vault(self) -> None:
        """What's left after losing the library: its contents gone, the backups kept."""
        with database.write() as connection:
            for table in ("run_events", "runs", "messages", "thread_documents"):
                connection.execute(database.metadata.tables[table].delete())
            connection.execute(database.notes.delete())
            connection.execute(database.threads.delete())
        shutil.rmtree(vault.index_dir(), ignore_errors=True)

    def test_it_brings_everything_back_reading_nothing(self, filled, monkeypatch):
        made = self.setup_it(filled)
        self.clean_vault()
        for name in filled.iterdir():
            os.utime(name, (1, 1))
        opened: list[str] = []
        real_open = builtins.open

        def counting(path, *given, **named):
            if str(filled) in str(path):
                opened.append(str(path))
            return real_open(path, *given, **named)

        monkeypatch.setattr(builtins, "open", counting)
        monkeypatch.setattr(Path, "read_bytes", _never_watched(filled, Path.read_bytes))
        calls = {"parse": 0, "embed": 0}
        monkeypatch.setattr(
            parse, "parse", lambda *_g, **_n: calls.__setitem__("parse", 1)
        )
        monkeypatch.setattr(
            embedder, "embed_documents", lambda *_g: calls.__setitem__("embed", 1)
        )
        backup.restore(made)
        assert calls == {"parse": 0, "embed": 0}
        assert opened == []
        assert notes.all_notes()[0]["body"] == "A note kept."
        assert threads.page()[0]["title"] == "New chat"
        assert cards.get(document_id())["summary"] == "Corrected by the owner."
        assert "rent" in _found_text()

    def test_it_saves_the_current_library_first(self, filled):
        made = self.setup_it(filled)
        before = len(backup.list_backups())
        backup.restore(made)
        assert len(backup.list_backups()) == before + 1

    def test_a_memory_deleted_since_doesnt_come_back(self, filled):
        stated = "My gate code is 4471."
        memory_id = add_memory(stated)
        made = backup.back_up()["id"]
        memories.delete(memory_id, None)
        backup.restore(made)
        assert not memories.is_active(memory_id)
        assert memories.normalized_hash(stated) in {
            stone["text_hash"] for stone in memories.tombstones()
        }

    def test_a_tombstone_in_the_backup_stays(self, filled):
        memory_id = add_memory("An older secret.")
        memories.delete(memory_id, None)
        made = backup.back_up()["id"]
        vault.tombstone_ledger().unlink()
        with database.write() as connection:
            connection.execute(database.memory_tombstones.delete())
        backup.restore(made)
        assert memories.tombstones()


class TestWhatCanGoWrong:
    def test_a_killed_copy_leaves_no_object_a_later_backup_would_trust(
        self, library, monkeypatch, tmp_path
    ):
        stored = vault.objects_dir() / "ab" / "abcd"
        stored.parent.mkdir(parents=True)
        stored.write_bytes(b"the whole file")
        real = shutil.copy2
        calls = []

        def dies(source, target, **_named):
            if not calls:
                calls.append(1)
                Path(target).write_bytes(b"the wh")
                raise OSError("killed")
            return real(source, target)

        monkeypatch.setattr(shutil, "copy2", dies)
        with pytest.raises(OSError, match="killed"):
            backup.back_up()
        made = backup.back_up()
        copy = backup.folder() / backup.OBJECTS / "ab" / "abcd"
        assert copy.read_bytes() == b"the whole file"
        assert made["id"]
        assert not list(backup.folder().rglob(f"*{backup.TEMPORARY}"))

    def test_the_oldest_of_seven_can_be_restored(self, filled):
        start = time.time()
        for day in range(backup.KEEP):
            backup.back_up(start + day)
        oldest = backup.list_backups()[-1]["id"]
        newest = backup.list_backups()[0]["id"]
        backup.restore(oldest)
        kept = {b["id"] for b in backup.list_backups()}
        assert len(kept) == backup.KEEP
        assert oldest not in kept  # it did its job, and retention applies again
        assert newest in kept

    def test_two_backups_in_one_second_are_both_kept(self, filled):
        now = time.time()
        first, second = backup.back_up(now), backup.back_up(now)
        assert first["id"] != second["id"]
        assert len(backup.list_backups()) == 2

    def test_a_backup_with_no_library_changes_nothing(self, filled):
        made = backup.back_up()["id"]
        (backup.folder() / made / backup.LIBRARY).unlink()
        with pytest.raises(refusals.Refused):
            backup.restore(made)
        assert count_of(database.documents) == 1
        assert len(backup.list_backups()) == 1

    def test_a_backup_of_something_else_changes_nothing(self, filled):
        made = backup.back_up()["id"]
        library_file = backup.folder() / made / backup.LIBRARY
        library_file.unlink()
        other = sqlite3.connect(library_file)
        other.execute("CREATE TABLE unrelated (x)")
        other.commit()
        other.close()
        with pytest.raises(refusals.Refused):
            backup.restore(made)
        assert count_of(database.documents) == 1

    def test_a_restore_into_another_vault_points_at_that_vault(
        self, library, monkeypatch, tmp_path
    ):
        dropped = tmp_path / "Dropped.txt"
        dropped.write_text("Copied in, then moved to a new Mac.\n")
        documents.import_paths([str(dropped)], states.STANDARD)
        drain()
        old_root = str(vault.root().resolve())
        made = backup.back_up()
        saved = tmp_path / "carried"
        shutil.copytree(backup.folder(), saved)
        other = tmp_path / "new-mac"
        monkeypatch.setenv("SECUREFOLIO_VAULT", str(other))
        migrations.upgrade()
        settings.put(settings.BACKUP_FOLDER, str(saved))
        backup.restore(made["id"])
        settings.put(settings.BACKUP_FOLDER, str(saved))
        with database.read() as connection:
            paths = list(
                connection.execute(
                    sa.select(database.document_locations.c.path)
                ).scalars()
            )
            home = connection.execute(
                sa.select(database.sources.c.path).where(
                    database.sources.c.id == states.LIBRARY_SOURCE_ID
                )
            ).scalar_one()
        assert paths
        assert not any(path.startswith(old_root) for path in paths)
        assert all(Path(path).is_file() for path in paths)
        assert home.startswith(str(other.resolve()))

    def test_a_restore_from_a_linked_vault_moves_the_paths_stored_through_the_link(
        self, library, monkeypatch, tmp_path
    ):
        elsewhere = tmp_path / "Volumes" / "Data" / "Library"
        elsewhere.mkdir(parents=True)
        linked = tmp_path / "Application Support" / "DocSynth"
        linked.parent.mkdir()
        linked.symlink_to(elsewhere, target_is_directory=True)
        database.dispose()
        monkeypatch.setenv("SECUREFOLIO_VAULT", str(linked))
        migrations.upgrade()
        sources.ensure_library_source()
        dropped = tmp_path / "Dropped.txt"
        dropped.write_text("Copied into a vault reached through a link.\n")
        documents.import_paths([str(dropped)], states.STANDARD)
        drain()
        settings.put(settings.BACKUP_FOLDER, str(vault.backups_dir()))
        made = backup.back_up()
        saved = tmp_path / "carried"
        shutil.copytree(backup.folder(), saved)
        other = tmp_path / "new-mac"
        database.dispose()
        monkeypatch.setenv("SECUREFOLIO_VAULT", str(other))
        migrations.upgrade()
        settings.put(settings.BACKUP_FOLDER, str(saved))
        backup.restore(made["id"])
        chosen = settings.get(settings.BACKUP_FOLDER)
        settings.put(settings.BACKUP_FOLDER, str(saved))
        with database.read() as connection:
            paths = list(
                connection.execute(
                    sa.select(database.document_locations.c.path)
                ).scalars()
            )
            home = connection.execute(
                sa.select(database.sources.c.path).where(
                    database.sources.c.id == states.LIBRARY_SOURCE_ID
                )
            ).scalar_one()
        assert paths
        for stored in [*paths, home, chosen]:
            assert not stored.startswith(str(linked))
            assert not stored.startswith(str(elsewhere))
            assert stored.startswith(str(other))
        assert all(Path(path).is_file() for path in paths)

    def test_watched_folders_stay_as_they_were(self, library, monkeypatch, tmp_path):
        root = vault.root().resolve()
        sibling = root.parent / f"{root.name}-reports"
        sibling.mkdir()
        (sibling / "Note.txt").write_text("A watched sibling of the vault.\n")
        sources.add_source(str(sibling), states.STANDARD)
        drain()
        made = backup.back_up()
        saved = tmp_path / "carried"
        shutil.copytree(backup.folder(), saved)
        monkeypatch.setenv("SECUREFOLIO_VAULT", str(tmp_path / "new-mac"))
        migrations.upgrade()
        settings.put(settings.BACKUP_FOLDER, str(saved))
        backup.restore(made["id"])
        assert str(sibling) in {s["path"] for s in sources.live()}
        with database.read() as connection:
            paths = set(
                connection.execute(
                    sa.select(database.document_locations.c.path)
                ).scalars()
            )
        assert str(sibling / "Note.txt") in paths

    @pytest.mark.parametrize(
        "damage",
        [
            "DROP TABLE messages",
            "ALTER TABLE notes DROP COLUMN body",
        ],
    )
    def test_a_damaged_schema_changes_nothing(self, filled, damage):
        made = backup.back_up()["id"]
        library_file = backup.folder() / made / backup.LIBRARY
        snapshot = sqlite3.connect(library_file)
        with snapshot:
            snapshot.execute(damage)
        snapshot.close()
        with pytest.raises(refusals.Refused):
            backup.restore(made)
        assert count_of(database.documents) == 1
        assert count_of(database.messages) == 0


class TestExclusiveAccess:
    def test_it_waits_for_a_write_under_way_and_refuses_new_ones(self, library):
        writing = threading.Event()
        finish = threading.Event()
        done = []

        def slow_write():
            with database.write():
                writing.set()
                finish.wait(5)
            done.append(True)

        thread = threading.Thread(target=slow_write)
        thread.start()
        writing.wait(5)
        with pytest.raises(database.Sealed), database.exclusive(0.05):
            pass  # the write above hasn't finished
        finish.set()
        thread.join()
        late: list[str] = []

        def late_write():
            try:
                with database.write():
                    late.append("wrote")
            except database.Sealed:
                late.append("refused")

        with database.exclusive(5):
            assert done == [True]
            with database.write():
                pass  # this thread may
            other = threading.Thread(target=late_write)
            other.start()
            other.join()
        assert late == ["refused"]
        with database.write():
            pass  # and it's open again

    def test_a_callback_from_before_a_restore_cant_settle_a_new_entry(self, library):
        def bound_elsewhere() -> contextvars.Context:
            database.bind_to_this_library()
            return contextvars.copy_context()

        old_run = contextvars.Context().run(bound_elsewhere)
        with database.exclusive(5):
            pass  # a restore has come and gone
        entry = egress.record(
            egress.Entry("anthropic:claude-haiku-5-5", "memory", egress.ATTEMPTED, [])
        )
        with pytest.raises(database.Sealed):
            old_run.run(egress.settle, entry, egress.SENT, cost_usd=99.0)
        with database.read() as connection:
            row = connection.execute(sa.select(database.egress_log)).mappings().one()
        assert row["outcome"] == egress.ATTEMPTED
        assert row["cost_usd"] == 0.0
        fresh = contextvars.Context().run(bound_elsewhere)
        fresh.run(egress.settle, entry, egress.SENT, cost_usd=1.0)

    def test_a_finite_get_is_waited_for_and_a_stream_isnt(self):
        gate = dependencies.MaintenanceGate(cast("Any", None))
        scope = {"method": "GET", "path": "/api/models"}
        assert dependencies._is_a_stream(scope) is False  # noqa: SLF001
        scope = {"method": "GET", "path": "/api/runs/r1/events"}
        assert dependencies._is_a_stream(scope) is True  # noqa: SLF001
        assert asyncio.run(gate.close(0)) is True
        assert asyncio.run(gate.close(0)) is False

    def test_a_request_that_wont_finish_stops_the_restore(self):
        gate = dependencies.MaintenanceGate(cast("Any", None))
        gate.inside = 2
        assert asyncio.run(gate.close(0.01)) is False
        assert gate.closed is False


class TestRestoreThroughTheApp:
    def test_it_stops_everything_and_opens_again(self, filled, app_client, monkeypatch):
        made = backup.back_up()["id"]
        seen = []
        real = backup.restore

        def checking(chosen):
            seen.append(app_client.app.state.gate.closed)
            seen.append(app_client.app.state.agent.saver is not None)
            real(chosen)

        monkeypatch.setattr(backup, "restore", checking)
        assert (
            app_client.post("/api/backups/restore", json={"id": made}).status_code
            == 200
        )
        assert seen[0] is True
        assert app_client.app.state.gate.closed is False
        assert app_client.get("/api/settings").status_code == 200

    def test_no_request_gets_in_while_it_runs(self, filled, app_client, monkeypatch):
        made = backup.back_up()["id"]
        answers = []

        def asks_during(_chosen):
            gate = app_client.app.state.gate
            answers.append(gate.closed)

        monkeypatch.setattr(backup, "restore", asks_during)
        app_client.post("/api/backups/restore", json={"id": made})
        assert answers == [True]


def _never_watched(folder, real):
    def reading(self, *given, **named):
        assert str(folder) not in str(self)
        return real(self, *given, **named)

    return reading


def _found_text() -> str:
    scope = search.Scope(include_private=True)
    return " ".join(item["text"] for item in search.retrieve("rent", scope, 3))
