"""Chats: their records, one run at a time, its stream, and what the owner answers."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend import agent_service, notes, policy, refusals, threads
from backend.dependencies import AppToken

router = APIRouter()
WAIT_SECONDS = 0.5
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
    """A chat with its transcript and any answer in progress."""
    return ThreadDetail(
        thread=threads.get(thread_id),
        messages=threads.transcript(thread_id),
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
    """Starts an answer, unless one runs or nothing can answer it now (409)."""
    thread = threads.get(thread_id)
    taint = bool(thread["private_tainted"])
    # Refused here only for what needs no network: no connection is tried, so the
    # run starts, and its first step shows within 250 ms, before routing asks,
    # waiting, whether the answer's provider can be reached.
    route = await asyncio.to_thread(policy.route, body.mode, False, taint, wait=False)
    if route.local or route.helper_local:
        # Ollama is asked only when a local model would run: a direct request learns,
        # as the route preview does, whether it can before it starts.
        await asyncio.to_thread(agent_service.refresh_ollama)
        route = await asyncio.to_thread(
            policy.route, body.mode, False, taint, wait=False
        )
    if route.blocked:
        raise refusals.conflict(route.blocked)
    return await _service(request).start(thread_id, body.text, body.mode)


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


async def run_stream(service: Any, run_id: str, after: int) -> AsyncIterator[str]:
    """Everything since `after`, then live. Reattaching just asks with a later id."""
    last = after
    while True:
        rows = await asyncio.to_thread(threads.events_after, run_id, last)
        for row in rows:
            last = row["id"]
            data = json.dumps(row["data"])
            yield f"id: {last}\nevent: {row['event']}\ndata: {data}\n\n"
        run = await asyncio.to_thread(threads.get_run, run_id)
        if not rows and run["state"] in (*FINISHED, threads.PAUSED):
            return
        if not rows:
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
    return notes.from_answer(message_id)


class Later(BaseModel):
    text: str


@router.post("/threads/{thread_id}/ask-when-indexed", operation_id="ask_when_indexed")
def ask_when_indexed(thread_id: str, body: Later, _: AppToken) -> Empty:
    """Keeps the question, to ask once the queue drains."""
    threads.ask_when_indexed(thread_id, body.text)
    return Empty()
