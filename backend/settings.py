"""The owner's settings, read fresh on each use."""

from typing import Any

import sqlalchemy as sa

from backend import database

OFFLINE = "offline"
SPEND_CAP = "spend_cap_usd"
MEMORY_ON = "memory_on"
ASK_FIRST = "deep_ask_first_usd"
MODEL_ANSWER = "model_answer"
MODEL_DEEP = "model_deep"
MODEL_HELPER = "model_helper"
MODEL_PAGE = "model_page"
MODEL_LOCAL = "model_local"  # before 0003: the one local tag, kept for old vaults
LOCAL_ANSWERS = "local_answers"
LOCAL_CARDS = "local_cards"
LOCAL_FIGURES = "local_figures"
LOCAL_JOBS = (LOCAL_ANSWERS, LOCAL_CARDS, LOCAL_FIGURES)
LOCAL_MEMORY_GB = "local_memory_gb"  # Ollama's own figure for each tag, once loaded
DEFAULT_SENSITIVITY = "default_sensitivity"
SETUP_DONE = "setup_done"
BACKUP_NIGHTLY = "backup_nightly"
BACKUP_FOLDER = "backup_folder"
BACKUP_LAST = "backup_last"
ANTHROPIC_WORKSPACE = "anthropic_workspace"
OPENAI_PROJECT = "openai_project"
ANTHROPIC_NEEDS_ID = "anthropic_needs_id"
OPENAI_NEEDS_ID = "openai_needs_id"
MODEL_TASKS = (MODEL_ANSWER, MODEL_DEEP, MODEL_HELPER, MODEL_PAGE)
OPENROUTER_FETCHED = "openrouter_fetched_at"  # when its list was last stored
OPENROUTER_FAILED = "openrouter_failed"  # why the last fetch failed, if it did
LOCAL = "local"  # as a model setting: this Mac's model

DEFAULTS: dict[str, Any] = {
    OFFLINE: False,
    SPEND_CAP: 25.0,
    MEMORY_ON: True,
    ASK_FIRST: 0.50,
    MODEL_ANSWER: "anthropic:claude-sonnet-5-5",
    MODEL_DEEP: "anthropic:claude-opus-5-5",
    MODEL_HELPER: "anthropic:claude-haiku-5-5",
    MODEL_PAGE: "anthropic:claude-sonnet-5-5",
    MODEL_LOCAL: "gemma4:26b",
    LOCAL_ANSWERS: "gemma4:26b",
    LOCAL_CARDS: "gemma4:26b",
    LOCAL_FIGURES: "gemma4:26b",
    LOCAL_MEMORY_GB: {},
    ANTHROPIC_NEEDS_ID: False,
    OPENAI_NEEDS_ID: False,
    DEFAULT_SENSITIVITY: "standard",
    SETUP_DONE: False,
    BACKUP_NIGHTLY: True,
}


def get(key: str) -> Any:
    with database.read() as connection:
        return get_in(connection, key)


def get_in(connection: Any, key: str) -> Any:
    """Within a read or write the caller holds."""
    table = database.settings
    value = connection.execute(
        sa.select(table.c.value).where(table.c.key == key)
    ).scalar()
    return DEFAULTS.get(key) if value is None else value


def put_in(connection: Any, key: str, value: Any) -> None:
    """Within a write the caller holds."""
    table = database.settings
    connection.execute(table.delete().where(table.c.key == key))
    connection.execute(table.insert().values(key=key, value=value))


def put(key: str, value: Any) -> None:
    table = database.settings
    with database.write() as connection:
        connection.execute(table.delete().where(table.c.key == key))
        connection.execute(table.insert().values(key=key, value=value))
