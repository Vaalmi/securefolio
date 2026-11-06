"""Publishes a revision, replays the index write, and queues what comes after."""

from backend import database, index, jobs, revisions
from backend.database import page_readings
from worker.content import PageFacts


def store_page_facts(revision_id: str, facts: list[PageFacts]) -> None:
    if not facts:
        return
    with database.write() as connection:
        connection.execute(
            page_readings.delete().where(page_readings.c.revision_id == revision_id)
        )
        connection.execute(
            page_readings.insert(),
            [
                {
                    "revision_id": revision_id,
                    "page": f.page,
                    "reasons": vars(f),
                    "state": "known",
                }
                for f in facts
            ],
        )


def publish(
    revision_id: str, job_id: str, make_indexed: bool, pages: int | None
) -> str:
    document_id = revisions.publish(revision_id, job_id, make_indexed, pages)
    index.replay_ops()
    return document_id


def queue_followups(document_id: str, source_id: str | None, kind: str) -> None:
    """Hard pages for a PDF or image, then the card; search never waits for either."""
    with database.write() as connection:
        if kind in {"PDF", "PNG", "JPEG", "HEIC", "WEBP"}:
            jobs.enqueue_in(
                connection,
                jobs.HARD_PAGES,
                jobs.PRIORITY_HARD_PAGES,
                document_id=document_id,
                source_id=source_id,
                key=f"hard:{document_id}",
            )
        jobs.enqueue_in(
            connection,
            jobs.ORGANIZE,
            jobs.PRIORITY_ORGANIZE,
            document_id=document_id,
            source_id=source_id,
            key=f"organize:{document_id}",
        )
