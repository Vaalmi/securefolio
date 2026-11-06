"""Migration 0003 on a vault as 0002 left it: the owner's choices carry over, and with
no settings changed every task still uses today's Claude model."""

import json
import sqlite3
import time

import sqlalchemy as sa
from alembic import command

from backend import database, settings, vault
from backend.migrations import run as migrations


def a_vault_at_0002() -> None:
    """0002 builds from today's table definitions, so the columns 0003 adds are taken
    out again, as they were on a vault made before it."""
    config = migrations._config()  # noqa: SLF001
    with database.engine().connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0002")
    with database.write() as connection:
        for table, column in (
            ("egress_log", "provider"),
            ("egress_log", "input_usd"),
            ("egress_log", "output_usd"),
            ("message_batches", "provider"),
            ("message_batches", "model"),
        ):
            connection.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
        connection.exec_driver_sql("DROP TABLE IF EXISTS openrouter_models")


def old_setting(key: str, value: object) -> None:
    with database.write() as connection:
        connection.execute(
            database.settings.insert().values(
                key=key, value=json.loads(json.dumps(value))
            )
        )


def old_rows() -> None:
    now = time.time()
    with database.write() as connection:
        connection.exec_driver_sql(
            "INSERT INTO egress_log (at, model, purpose, outcome, document_ids) "
            "VALUES (?, 'claude-sonnet-5-5', 'answer', 'sent', '[]')",
            (now,),
        )
        connection.exec_driver_sql(
            "INSERT INTO message_batches (id, submission_id, state, created_at) "
            "VALUES ('b1', 's1', 'submitted', ?)",
            (now,),
        )


def test_with_nothing_changed_every_task_keeps_todays_claude_model():
    a_vault_at_0002()
    old_rows()
    migrations.upgrade()
    assert settings.get(settings.MODEL_ANSWER) == "anthropic:claude-sonnet-5-5"
    assert settings.get(settings.MODEL_DEEP) == "anthropic:claude-opus-5-5"
    assert settings.get(settings.MODEL_HELPER) == "anthropic:claude-haiku-5-5"
    assert settings.get(settings.MODEL_PAGE) == "anthropic:claude-sonnet-5-5"
    with database.read() as connection:
        logged = connection.execute(sa.select(database.egress_log)).mappings().one()
        batch = connection.execute(sa.select(database.message_batches)).mappings().one()
    assert (logged["provider"], logged["model"]) == (
        "anthropic",
        "anthropic:claude-sonnet-5-5",
    )
    assert (batch["provider"], batch["model"]) == (
        "anthropic",
        "anthropic:claude-haiku-5-5",
    )


def test_the_owners_choices_become_provider_ids():
    a_vault_at_0002()
    old_setting("model_answer", "claude-opus-5-5")
    old_setting("model_helper", "local")
    old_setting("model_local", "qwen9:14b")
    old_rows()
    migrations.upgrade()
    assert settings.get(settings.MODEL_ANSWER) == "anthropic:claude-opus-5-5"
    assert settings.get(settings.MODEL_HELPER) == settings.LOCAL
    for job in settings.LOCAL_JOBS:
        assert settings.get(job) == "qwen9:14b"
    with database.read() as connection:
        batch = connection.execute(sa.select(database.message_batches)).mappings().one()
    # A batch only ever went to a cloud model, so a local helper means Haiku.
    assert batch["model"] == "anthropic:claude-haiku-5-5"


def test_the_batch_takes_the_helper_it_was_sent_with():
    a_vault_at_0002()
    old_setting("model_helper", "claude-sonnet-5-5")
    old_rows()
    migrations.upgrade()
    with database.read() as connection:
        batch = connection.execute(sa.select(database.message_batches)).mappings().one()
    assert batch["model"] == "anthropic:claude-sonnet-5-5"


def test_openrouters_table_is_made():
    a_vault_at_0002()
    migrations.upgrade()
    with database.read() as connection:
        assert "openrouter_models" in sa.inspect(connection).get_table_names()


def test_the_egress_log_keeps_each_calls_accepted_price():
    a_vault_at_0002()
    old_rows()
    migrations.upgrade()
    with database.read() as connection:
        found = {c["name"] for c in sa.inspect(connection).get_columns("egress_log")}
        row = connection.execute(sa.select(database.egress_log)).mappings().one()
    assert {"input_usd", "output_usd"} <= found
    assert (row["input_usd"], row["output_usd"]) == (None, None)  # priced as today


def test_the_backup_is_taken_first_and_holds_the_old_values():
    a_vault_at_0002()
    old_setting("model_answer", "claude-opus-5-5")
    migrations.upgrade()
    backups = list(vault.root().glob("library.before-0003.db"))
    assert backups
    saved = sqlite3.connect(backups[0]).execute(
        "SELECT value FROM settings WHERE key = 'model_answer'"
    )
    assert json.loads(saved.fetchone()[0]) == "claude-opus-5-5"


def test_migrating_again_changes_nothing():
    a_vault_at_0002()
    old_setting("model_answer", "claude-opus-5-5")
    migrations.upgrade()
    migrations.upgrade()
    assert settings.get(settings.MODEL_ANSWER) == "anthropic:claude-opus-5-5"


def test_a_vault_made_after_0002_still_migrates():
    """0002 built these tables from today's definitions: nothing is added twice."""
    config = migrations._config()  # noqa: SLF001
    with database.engine().connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0002")
    migrations.upgrade()
    assert settings.get(settings.MODEL_ANSWER) == "anthropic:claude-sonnet-5-5"
