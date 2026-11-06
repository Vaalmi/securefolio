"""Brings library.db up to date at startup, after a backup of it taken through
SQLite's backup API, so committed data still in the WAL is included."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from backend import database, vault


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent))
    return config


def _backup(destination: Path) -> None:
    source = sqlite3.connect(vault.library_db())
    target = sqlite3.connect(destination)
    with source, target:
        source.backup(target)
    source.close()
    target.close()


def upgrade() -> None:
    config = _config()
    head = ScriptDirectory.from_config(config).get_current_head()
    with database.engine().connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current == head:
        return
    if current is not None:
        _backup(vault.library_db().with_suffix(f".before-{head}.db"))
    with database.engine().connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
