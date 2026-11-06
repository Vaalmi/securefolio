"""Notes with citations, and v1's saved queries."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend import notes
from backend.dependencies import AppToken

router = APIRouter()


class Empty(BaseModel):
    pass


class NewNote(BaseModel):
    body: str
    document_id: str | None = None
    heading: str | None = None  # none, or empty: the text's first 40 characters


class NoteEdit(BaseModel):
    heading: str  # changed with the text, together
    body: str


@router.get("/notes", operation_id="list_notes")
def list_notes(_: AppToken, document: str | None = None) -> list[dict[str, Any]]:
    """Every note, or a document's, with where each citation now opens."""
    return notes.all_notes(document)


@router.post("/notes", operation_id="create_note")
def create_note(body: NewNote, _: AppToken) -> dict[str, Any]:
    """Saves a note, with its heading, carrying its document's sensitivity."""
    return notes.create(body.body, body.document_id, body.heading)


@router.patch("/notes/{note_id}", operation_id="edit_note")
def edit_note(note_id: str, body: NoteEdit, _: AppToken) -> Empty:
    """Changes a note's heading and text together; its citations are kept."""
    notes.edit(note_id, body.body, body.heading)
    return Empty()


@router.delete("/notes/{note_id}", operation_id="delete_note")
def delete_note(note_id: str, _: AppToken) -> Empty:
    """Deletes a note."""
    notes.delete(note_id)
    return Empty()


@router.get("/saved-queries", operation_id="list_saved_queries")
def list_saved_queries(_: AppToken) -> list[dict[str, Any]]:
    """The searches saved in v1, which the Notes page runs."""
    return notes.queries()
