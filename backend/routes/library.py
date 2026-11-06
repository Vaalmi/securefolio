"""The library list, its facets and its counts."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend import library
from backend.dependencies import AppToken

router = APIRouter()


class LibraryWindow(BaseModel):
    rows: list[dict[str, Any]]
    total: int
    offset: int


class Facets(BaseModel):
    types: list[dict[str, Any]]
    folders: list[dict[str, Any]]
    tags: list[dict[str, Any]]
    collections: list[dict[str, Any]]


class LibraryCounts(BaseModel):
    total: int
    types: dict[str, int]


class ListParams(BaseModel):
    view: Literal["recents", "starred"] | None = None
    doc_type: str | None = None
    source: str | None = None
    collection: str | None = None
    tag: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    name: str | None = None
    include_private: bool = True
    sort: Literal["title", "type", "date", "size"] = "date"
    descending: bool = True
    offset: int = 0
    limit: int = 200

    def filters(self) -> library.Filters:
        return library.Filters(
            view=self.view,
            doc_type=self.doc_type,
            source_id=self.source,
            collection_id=self.collection,
            tag=self.tag,
            date_from=self.date_from,
            date_to=self.date_to,
            name=self.name,
            include_private=self.include_private,
        )


Params = Annotated[ListParams, Query()]


@router.get("/library", operation_id="list_library")
def list_library(params: Params, _: AppToken) -> LibraryWindow:
    """One window of the list, sorted and filtered, with the total."""
    return LibraryWindow(
        **library.window(
            params.filters(),
            params.sort,
            params.descending,
            params.offset,
            params.limit,
        )
    )


@router.get("/library/ids", operation_id="list_library_ids")
def list_library_ids(params: Params, _: AppToken) -> list[str]:
    """Every id these filters match, for Select all."""
    return library.ids(params.filters(), params.sort, params.descending)


@router.get("/library/facets", operation_id="get_library_facets")
def get_library_facets(params: Params, _: AppToken) -> Facets:
    """The choices for each filter, given the other filters."""
    return Facets(**library.facets(params.filters()))


@router.get("/library/counts", operation_id="get_library_counts")
def get_library_counts(_: AppToken) -> LibraryCounts:
    """The sidebar's counts."""
    return LibraryCounts(**library.sidebar_counts())
