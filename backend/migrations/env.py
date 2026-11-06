"""Alembic's entry: runs on the engine the caller hands it."""

from alembic import context

from backend.database import metadata

connection = context.config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=metadata,
    render_as_batch=True,
    # LangGraph owns its own tables in the same file.
    include_name=lambda name, kind, _parent: kind != "table" or name in metadata.tables,
)
with context.begin_transaction():
    context.run_migrations()
