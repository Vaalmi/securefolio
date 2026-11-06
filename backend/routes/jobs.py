"""The import tray: its rows, its counts, and Retry and Cancel."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend import documents, tray
from backend.dependencies import AppToken

router = APIRouter()
POLL_SECONDS = 1.0


class Empty(BaseModel):
    pass


class TrayRow(BaseModel):
    id: str
    job_id: str | None
    document_id: str
    file: str
    folder: str
    status: Literal["Queued", "Indexing", "Completed", "Failed", "Skipped"]
    detail: str
    skip_kind: str | None


class TrayCounts(BaseModel):
    done: int
    total: int
    active: int
    completed: int
    failed: int
    skipped: int
    latest: float


@router.get("/jobs", operation_id="list_jobs")
def list_jobs(
    _: AppToken,
    tab: Literal["all", "active", "completed", "failed", "skipped"] = "all",
    limit: Annotated[int, Query(ge=1)] = tray.LIMIT,
) -> list[TrayRow]:
    """The tray's rows for one tab, as many of each kind as asked for."""
    return [TrayRow(**row) for row in tray.rows(tab, limit)]


@router.get("/jobs/counts", operation_id="get_job_counts")
def get_job_counts(_: AppToken) -> TrayCounts:
    """The indexing line and the tray's tab counts."""
    return TrayCounts(**tray.counts())


@router.post("/jobs/{job_id}/cancel", operation_id="cancel_job")
def cancel_job(job_id: str, _: AppToken) -> Empty:
    """Cancels a job; nothing is left half-indexed."""
    documents.cancel_job(job_id)
    return Empty()


@router.post("/jobs/{job_id}/retry", operation_id="retry_job")
def retry_job(job_id: str, _: AppToken) -> Empty:
    """Queues a failed job again."""
    documents.retry_job(job_id)
    return Empty()


def _changed(last: dict[str, Any] | None) -> dict[str, Any] | None:
    now = tray.counts()
    return now if now != last else None


async def counts_events(rounds: int | None = None) -> AsyncIterator[str]:
    """Sends the counts whenever they change. The app reloads its rows on each."""
    last: dict[str, Any] | None = None
    done = 0
    while rounds is None or done < rounds:
        fresh = await asyncio.to_thread(_changed, last)
        if fresh is not None:
            last = fresh
            yield f"event: counts\ndata: {json.dumps(fresh)}\n\n"
        else:
            yield ": still here\n\n"
        done += 1
        if rounds is None or done < rounds:
            await asyncio.sleep(POLL_SECONDS)


@router.get("/jobs/events", operation_id="follow_jobs")
def follow_jobs(_: AppToken) -> StreamingResponse:
    """Follows the queue as a server-sent event stream."""
    return StreamingResponse(counts_events(), media_type="text/event-stream")
