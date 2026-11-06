"""The collections the owner makes, and the documents in them."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend import collections
from backend.dependencies import AppToken

router = APIRouter()


class Empty(BaseModel):
    pass


class NewCollection(BaseModel):
    name: str


class Members(BaseModel):
    document_ids: list[str]


@router.get("/collections", operation_id="list_collections")
def list_collections(_: AppToken) -> list[dict[str, Any]]:
    """Every collection, with its count and a few titles."""
    return collections.all_collections()


@router.post("/collections", operation_id="create_collection")
def create_collection(body: NewCollection, _: AppToken) -> dict[str, Any]:
    """Makes a new collection."""
    return collections.create(body.name)


@router.post("/collections/{collection_id}/add", operation_id="add_to_collection")
def add_to_collection(collection_id: str, body: Members, _: AppToken) -> Empty:
    """Adds documents to a collection."""
    collections.add_documents(collection_id, body.document_ids)
    return Empty()


@router.post(
    "/collections/{collection_id}/remove", operation_id="remove_from_collection"
)
def remove_from_collection(collection_id: str, body: Members, _: AppToken) -> Empty:
    """Takes documents out of a collection; they stay in the library."""
    collections.remove_documents(collection_id, body.document_ids)
    return Empty()
