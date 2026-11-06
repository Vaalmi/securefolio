"""The library's tables."""

from alembic import op

from backend import database

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = [
    "sources",
    "documents",
    "document_locations",
    "revisions",
    "elements",
    "chunks",
    "page_readings",
    "jobs",
    "job_events",
    "cards",
    "collections",
    "collection_items",
    "notes",
    "saved_queries",
    "threads",
    "thread_documents",
    "thread_evidence",
    "messages",
    "runs",
    "run_events",
    "memories",
    "memory_tombstones",
    "message_batches",
    "batch_items",
    "egress_log",
    "v1_imports",
    "model_downloads",
    "index_generations",
    "index_ops",
]


def upgrade() -> None:
    connection = op.get_bind()
    database.create_tables(connection, TABLES)
    connection.exec_driver_sql(database.FTS_DDL)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP TABLE documents_fts")
    for name in reversed(TABLES):
        op.drop_table(name)
