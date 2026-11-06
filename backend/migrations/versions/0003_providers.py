"""Three cloud providers and a local model for each job.

The egress log and the message batches gain their provider, and batches their model.
The egress log also keeps the price each call was accepted at, and a run the owner's
answer to each card it asked.
OpenRouter's list gets a table. The task models become provider:model ids, and the
one local tag becomes the three local jobs', so the owner's choices carry over.

0002 builds its tables from the current definitions, so on a vault made after this
change those columns are already there: each is added only where it's missing."""

import sqlalchemy as sa
from alembic import op

from backend import database

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ANTHROPIC = "anthropic"  # every row before this was Anthropic's
TASK_KEYS = ("model_answer", "model_deep", "model_helper", "model_page")
LOCAL_JOBS = ("local_answers", "local_cards", "local_figures")


def _columns(connection: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(connection).get_columns(table)}


def _setting(connection: sa.Connection, key: str) -> object:
    table = database.settings
    return connection.execute(
        sa.select(table.c.value).where(table.c.key == key)
    ).scalar()


def _put(connection: sa.Connection, key: str, value: object) -> None:
    table = database.settings
    connection.execute(table.delete().where(table.c.key == key))
    connection.execute(table.insert().values(key=key, value=value))


def _add_egress_columns(connection: sa.Connection) -> None:
    """Its provider, and the price each call was accepted at."""
    found = _columns(connection, "egress_log")
    if "provider" not in found:
        op.add_column(
            "egress_log",
            sa.Column("provider", sa.String, nullable=False, server_default=ANTHROPIC),
        )
    for price in ("input_usd", "output_usd"):
        if price not in found:
            op.add_column("egress_log", sa.Column(price, sa.Float))


def upgrade() -> None:
    connection = op.get_bind()
    _add_egress_columns(connection)
    if "answered" not in _columns(connection, "runs"):  # the owner's answers to cards
        op.add_column("runs", sa.Column("answered", sa.JSON))
    connection.execute(
        sa.text(
            "UPDATE egress_log SET model = 'anthropic:' || model "
            "WHERE model NOT LIKE '%:%'"
        )
    )
    found = _columns(connection, "message_batches")
    if "provider" not in found:
        op.add_column(
            "message_batches",
            sa.Column("provider", sa.String, nullable=False, server_default=ANTHROPIC),
        )
    if "model" not in found:
        op.add_column("message_batches", sa.Column("model", sa.String))
    helper = str(_setting(connection, "model_helper") or "claude-haiku-5-5")
    if helper == "local":
        helper = "claude-haiku-5-5"  # a batch only ever went to a cloud model
    helper = helper if ":" in helper else f"anthropic:{helper}"
    connection.execute(
        sa.text("UPDATE message_batches SET model = :model WHERE model IS NULL"),
        {"model": helper},
    )
    database.create_tables(connection, ["openrouter_models"])
    for key in TASK_KEYS:
        value = _setting(connection, key)
        if isinstance(value, str) and value != "local" and ":" not in value:
            _put(connection, key, f"anthropic:{value}")
    local = _setting(connection, "model_local")
    if local is not None:
        for job in LOCAL_JOBS:
            if _setting(connection, job) is None:
                _put(connection, job, local)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP TABLE openrouter_models")
    for key in TASK_KEYS:
        value = _setting(connection, key)
        if isinstance(value, str) and value.startswith("anthropic:"):
            _put(connection, key, value.removeprefix("anthropic:"))
    connection.execute(
        sa.text(
            "UPDATE egress_log SET model = substr(model, 11) "
            "WHERE model LIKE 'anthropic:%'"
        )
    )
