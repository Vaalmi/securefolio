"""The database's settings on every connection, and migrating at startup."""

import contextlib
import json
import sqlite3

import sqlalchemy as sa
from alembic.util.exc import CommandError

from backend import database, settings, vault
from backend.migrations import run as migrations


def test_each_connection_has_the_pragmas():
    migrations.upgrade()
    with database.read() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_migrating_twice_changes_nothing():
    migrations.upgrade()
    migrations.upgrade()
    with database.read() as connection:
        names = sa.inspect(connection).get_table_names()
    assert "settings" in names
    assert "alembic_version" in names


def test_a_migration_copies_the_database_first():
    migrations.upgrade()
    settings.put("k", "only in the WAL")
    with database.write() as connection:
        connection.exec_driver_sql("UPDATE alembic_version SET version_num='0000'")
    # A stale version forces the copy; the script directory won't find 0000, which
    # is fine: the copy happens first.
    with contextlib.suppress(CommandError):
        migrations.upgrade()
    backups = list(vault.root().glob("library.before-*.db"))
    assert backups
    # The cached engine still holds a connection, so this write is only in the WAL.
    saved = sqlite3.connect(backups[0]).execute("SELECT value FROM settings")
    assert json.loads(saved.fetchone()[0]) == "only in the WAL"


def test_settings_read_a_default_then_what_was_put():
    migrations.upgrade()
    assert settings.get(settings.OFFLINE) is False
    settings.put(settings.OFFLINE, True)
    assert settings.get(settings.OFFLINE) is True


def test_a_failed_write_rolls_back():
    migrations.upgrade()
    try:
        with database.write() as connection:
            connection.execute(database.settings.insert().values(key="k", value=1))
            raise RuntimeError
    except RuntimeError:
        pass
    assert settings.get("k") is None
