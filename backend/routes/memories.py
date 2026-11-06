"""What SecureFolio remembers, which the owner can see, edit and delete."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend import agent_service, memories, settings
from backend.dependencies import AppToken

router = APIRouter()


class Empty(BaseModel):
    pass


class MemoryEdit(BaseModel):
    text: str | None = None
    pinned: bool | None = None


class MemorySwitch(BaseModel):
    on: bool


@router.get("/memories", operation_id="list_memories")
def list_memories(_: AppToken) -> list[dict[str, Any]]:
    """Everything remembered, with where each came from."""
    rows = memories.all_active()
    return [{k: v for k, v in row.items() if k != "embedding"} for row in rows]


@router.patch("/memories/{memory_id}", operation_id="edit_memory")
def edit_memory(memory_id: str, body: MemoryEdit, _: AppToken) -> Empty:
    """Changes a memory's words, or pins it."""
    if body.text is not None:
        agent_service.edit_memory(memory_id, body.text)
    if body.pinned is not None:
        memories.set_pinned(memory_id, body.pinned)
    return Empty()


@router.delete("/memories/{memory_id}", operation_id="delete_memory")
def delete_memory(memory_id: str, _: AppToken) -> Empty:
    """Deletes a memory for good: a tombstone keeps it from coming back."""
    agent_service.delete_memory(memory_id)
    return Empty()


@router.get("/memories/switch", operation_id="get_memory_switch")
def get_memory_switch(_: AppToken) -> MemorySwitch:
    """Whether SecureFolio remembers anything at all."""
    return MemorySwitch(on=bool(settings.get(settings.MEMORY_ON)))


@router.put("/memories/switch", operation_id="set_memory_switch")
def set_memory_switch(body: MemorySwitch, _: AppToken) -> Empty:
    """Turns memory on or off everywhere."""
    settings.put(settings.MEMORY_ON, body.on)
    return Empty()
