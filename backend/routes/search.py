"""One box for search and questions."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend import agent_service, policy, search
from backend.dependencies import AppToken

router = APIRouter()


class SearchResults(BaseModel):
    documents: list[dict[str, Any]]
    passages: list[dict[str, Any]]
    hidden_private: int
    semantic: bool


class AskRoute(BaseModel):
    """Who answers, and why when it's local or can't answer, for the Ask button."""

    local: bool
    model: str
    name: str
    provider: str | None
    reason: str | None
    blocked: str | None
    helper_local: bool
    helper_name: str  # the helper's model name, or the local tag
    deep_cloud: bool
    needs_key: bool  # local only for want of the chosen model's provider key


class RouteParams(BaseModel):
    mode: Literal["standard", "deep", "private"] = "standard"
    include_private: bool = False
    thread: str | None = None
    collection: str | None = None
    documents: list[str] | None = None


class Empty(BaseModel):
    pass


class SearchParams(BaseModel):
    q: str = ""
    include_private: bool = False
    source: str | None = None
    doc_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    collection: str | None = None
    tag: str | None = None
    documents: list[str] | None = None


@router.get("/search", operation_id="search")
def search_library(
    params: Annotated[SearchParams, Query()], _: AppToken
) -> SearchResults:
    """Documents and Passages for what's typed so far, without waiting on a model."""
    scope = search.Scope(
        include_private=params.include_private,
        source_id=params.source,
        doc_type=params.doc_type,
        date_from=params.date_from,
        date_to=params.date_to,
        collection_id=params.collection,
        tag=params.tag,
        document_ids=tuple(params.documents) if params.documents is not None else None,
    )
    return SearchResults(**search.run(params.q, scope))


@router.post("/search/warm", operation_id="warm_search")
def warm_search(_: AppToken) -> Empty:
    """Starts loading the search model; focusing a search box asks for this."""
    search.warm()
    return Empty()


@router.get("/ask/route", operation_id="get_ask_route")
def get_ask_route(params: Annotated[RouteParams, Query()], _: AppToken) -> AskRoute:
    """Who would answer in this scope, thread and mode, for the Ask button's label."""
    agent_service.refresh_ollama()
    private = params.include_private or policy.scope_is_private(
        params.documents, params.collection
    )
    thread_private = params.thread is not None and policy.thread_is_tainted(
        params.thread
    )
    # The Ask box shows whether Deep would go to the cloud, so only it asks.
    route = policy.route(params.mode, private, thread_private, preview_deep=True)
    return AskRoute(
        local=route.local,
        model=route.model,
        name=route.name,
        provider=route.provider,
        reason=route.reason,
        blocked=route.blocked,
        helper_local=route.helper_local,
        helper_name=policy.model_name(route.helper_model),
        deep_cloud=route.deep_cloud,
        needs_key=route.needs_key,
    )
