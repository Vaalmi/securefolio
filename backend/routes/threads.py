"""Chats: their records, one run at a time, its stream, and what the owner answers."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend import agent_service, notes, reports, threads
from backend.dependencies import AppToken

router = APIRouter()
WAIT_SECONDS = 0.5
# How long a stopped run may go without its closing event before its stream ends.
CLOSING_GRACE_SECONDS = 5
FINISHED = (threads.COMPLETED, threads.STOPPED, threads.FAILED, threads.SUPERSEDED)
Mode = Literal["standard", "deep", "private"]


class Empty(BaseModel):
    pass


class ScopeFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str


class LibraryScope(ScopeFields):
    kind: Literal["library"]
    include_private: bool


class CollectionScope(ScopeFields):
    kind: Literal["collection"]
    id: str = Field(min_length=1)


class DocumentsScope(ScopeFields):
    kind: Literal["documents", "search"]
    ids: list[str] = Field(min_length=1)


Scope = Annotated[
    LibraryScope | CollectionScope | DocumentsScope, Field(discriminator="kind")
]


class NewThread(BaseModel):
    scope: Scope | None = None
    mode: Mode = "standard"


class ThreadPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    memory_off: bool | None = None


class NewMessage(BaseModel):
    text: str
    mode: Mode = "standard"


class ResumeBody(BaseModel):
    card_id: str
    choice: Literal["local", "leave", "deep", "standard"]


class ScopeBody(BaseModel):
    scope: Scope
    consented: bool = False


class ThreadDetail(BaseModel):
    thread: dict[str, Any]
    messages: list[dict[str, Any]]
    active_run: dict[str, Any] | None
    last_failure: dict[str, Any] | None


def _service(request: Request) -> Any:
    return request.app.state.agent


@router.get("/threads", operation_id="list_threads")
def list_threads(_: AppToken, offset: int = 0) -> list[dict[str, Any]]:
    """Chats, pinned first and then the latest, a page at a time."""
    return threads.page(max(offset, 0))


@router.post("/threads", operation_id="create_thread")
def create_thread(body: NewThread, _: AppToken) -> dict[str, Any]:
    """Starts a chat. A Private scope makes it Private from the start."""
    scope = body.scope.model_dump() if body.scope else None
    return threads.create(scope, body.mode)


@router.get("/threads/{thread_id}", operation_id="get_thread")
def get_thread(thread_id: str, _: AppToken) -> ThreadDetail:
    """A chat with its transcript, its answers' reports, and any answer in progress."""
    thread = threads.get(thread_id)
    reported = reports.reported_in(thread_id)
    return ThreadDetail(
        thread=thread,
        messages=[
            {
                **m,
                "copy_text": agent_service.copy_text(m["answer"]),
                "reported": m["id"] in reported,
            }
            if m.get("answer")
            else m
            for m in threads.transcript(thread_id)
        ],
        active_run=threads.active_run(thread_id),
        last_failure=threads.last_failure(thread_id),
    )


@router.patch("/threads/{thread_id}", operation_id="update_thread")
def update_thread(thread_id: str, body: ThreadPatch, _: AppToken) -> Empty:
    """Renames or pins a chat, or turns its memory off or on."""
    threads.update(thread_id, **body.model_dump(exclude_none=True))
    return Empty()


@router.delete("/threads/{thread_id}", operation_id="delete_thread")
async def delete_thread(thread_id: str, request: Request, _: AppToken) -> Empty:
    """Deletes a chat and its checkpoints."""
    await _service(request).delete_thread(thread_id)
    return Empty()


@router.post("/threads/{thread_id}/scope", operation_id="set_thread_scope")
def set_thread_scope(thread_id: str, body: ScopeBody, _: AppToken) -> dict[str, Any]:
    """Changes a chat's scope, asking first if that adds Private documents."""
    return threads.set_scope(thread_id, body.scope.model_dump(), body.consented)


@router.post("/threads/{thread_id}/messages", operation_id="send_message")
async def send_message(
    thread_id: str, body: NewMessage, request: Request, _: AppToken
) -> dict[str, Any]:
    """Starts an answer, or passages found when no model can answer it now."""
    threads.get(thread_id)  # a chat that doesn't exist is refused here
    # Nothing is asked here, Ollama included, so the run starts and its first step
    # shows within 250 ms; the run routes, and a route nothing can answer finds
    # passages rather than being refused.
    return await _service(request).start(thread_id, body.text, body.mode)


class PlaceFields(BaseModel):
    # Whole numbers as sent: "3" or 2.5 is never read as another page.
    model_config = ConfigDict(extra="forbid", strict=True)


class PagePlace(PlaceFields):
    kind: Literal["page"]
    page: int


class SlidePlace(PlaceFields):
    kind: Literal["slide"]
    slide: int


class MessagePlace(PlaceFields):
    kind: Literal["message"]
    message: int


class LinesPlace(PlaceFields):
    kind: Literal["lines"]
    start: int
    end: int


class CellsPlace(PlaceFields):
    kind: Literal["cells"]
    sheet: str
    cells: str


# Its shape by kind; whether the document has that place is `reports`' to say.
Place = Annotated[
    PagePlace | SlidePlace | MessagePlace | LinesPlace | CellsPlace,
    Field(discriminator="kind"),
]


class ReportBody(BaseModel):
    """What "This was wrong" says: the right document and the place in it, or none
    when it isn't in the library, and the fact and a note, both optional."""

    document_id: str | None = None
    place: Place | None = None
    fact: str | None = None
    note: str | None = None


class Reported(BaseModel):
    id: str
    private: bool
    why: str | None


class ReportPrivacy(BaseModel):
    why: str | None


@router.post(
    "/threads/{thread_id}/messages/{message_id}/report", operation_id="report_answer"
)
def report_answer(
    thread_id: str, message_id: str, body: ReportBody, _: AppToken
) -> Reported:
    """Saves "This was wrong" as a question in the owner's own set, on this Mac."""
    place = body.place.model_dump() if body.place else None
    said = reports.Report(body.document_id, place, body.fact, body.note)
    return Reported(**reports.save(thread_id, message_id, said))


@router.get(
    "/threads/{thread_id}/messages/{message_id}/report/privacy",
    operation_id="get_report_privacy",
)
def get_report_privacy(
    thread_id: str, message_id: str, _: AppToken, document_id: str | None = None
) -> ReportPrivacy:
    """Whether a report with this document picked would be Private, and why."""
    return ReportPrivacy(why=reports.privacy(thread_id, message_id, document_id))


@router.post("/threads/{thread_id}/resume", operation_id="resume_thread")
async def resume_thread(
    thread_id: str, body: ResumeBody, request: Request, _: AppToken
) -> dict[str, Any]:
    """Continues a paused answer with what the owner chose."""
    return await _service(request).resume(thread_id, body.card_id, body.choice)


@router.post("/threads/{thread_id}/stop", operation_id="stop_thread")
async def stop_thread(thread_id: str, request: Request, _: AppToken) -> Empty:
    """Stops the answer and puts the chat back as it was before the question."""
    await _service(request).stop(thread_id)
    return Empty()


def _ended(run: dict[str, Any], sent: int) -> bool:
    """Whether a run that has stopped has had its closing event sent: a run moves to its
    last state first and writes `done`, or a pause's card, just after."""
    closing = "card" if run["state"] == threads.PAUSED else "done"
    newest = threads.last_event(run["id"])
    return newest is not None and newest[1] == closing and newest[0] <= sent


async def run_stream(service: Any, run_id: str, after: int) -> AsyncIterator[str]:
    """Everything since `after`, then live, until the run's closing event has been sent.
    Reattaching just asks with a later id."""
    last = after
    stopped_at: float | None = None
    while True:
        rows = await asyncio.to_thread(threads.events_after, run_id, last)
        for row in rows:
            last = row["id"]
            data = json.dumps(row["data"])
            yield f"id: {last}\nevent: {row['event']}\ndata: {data}\n\n"
        if rows:
            stopped_at = None
            continue
        run = await asyncio.to_thread(threads.get_run, run_id)
        if run["state"] not in (*FINISHED, threads.PAUSED):
            stopped_at = None  # running again, as after a pause's card is answered
        else:
            # Stopped with nothing new: done once its closing event is out, or once a
            # run that stopped without writing one has had its grace.
            if stopped_at is None:
                stopped_at = time.monotonic()
            late = time.monotonic() - stopped_at > CLOSING_GRACE_SECONDS
            if late or await asyncio.to_thread(_ended, run, last):
                return
        await service.wait_for_event(run_id, WAIT_SECONDS)


@router.get("/runs/{run_id}/events", operation_id="follow_run")
def follow_run(
    run_id: str, request: Request, _: AppToken, after: int = 0
) -> StreamingResponse:
    """Follows an answer as a server-sent event stream, from `after`."""
    threads.get_run(run_id)
    return StreamingResponse(
        run_stream(_service(request), run_id, after), media_type="text/event-stream"
    )


@router.post("/messages/{message_id}/note", operation_id="save_answer_as_note")
def save_answer_as_note(message_id: str, _: AppToken) -> dict[str, Any]:
    """Saves an answer to Notes, with its citations."""
    return notes.from_answer(message_id, agent_service.note_text)


class Later(BaseModel):
    text: str


@router.post("/threads/{thread_id}/ask-when-indexed", operation_id="ask_when_indexed")
def ask_when_indexed(thread_id: str, body: Later, _: AppToken) -> Empty:
    """Keeps the question, to ask once the queue drains."""
    threads.ask_when_indexed(thread_id, body.text)
    return Empty()
