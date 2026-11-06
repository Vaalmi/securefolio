"""One document: its record, its bytes for the viewer, and what can be done to it."""

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from backend import (
    cards,
    collections,
    documents,
    elements,
    files,
    jobs,
    model_catalog,
    policy,
    providers,
    refusals,
    spend,
    states,
)
from backend.dependencies import AppToken

router = APIRouter()


class Empty(BaseModel):
    pass


class DocumentDetail(BaseModel):
    document: dict[str, Any]
    card: dict[str, Any] | None
    locations: list[dict[str, Any]]
    collections: list[dict[str, str]]
    view_state: str
    cloud_page_allowed: bool  # Standard and not Offline: the action is offered
    has_live_work: bool


class StoredText(BaseModel):
    elements: list[dict[str, Any]]
    total: int


class CitedElement(BaseModel):
    element: dict[str, Any] | None
    current: bool


class Queued(BaseModel):
    job_id: str


class PageCost(BaseModel):
    """Reading one page in the cloud: the page model, its price and the most it costs,
    or why it's unavailable now (no ready key for its provider, or no known price)."""

    model: str
    model_name: str
    provider: str
    input_usd: float | None  # per million tokens; None: no known price
    output_usd: float | None
    cost_usd: float | None
    unavailable: str | None


class StarBody(BaseModel):
    starred: bool


class RemoveBody(BaseModel):
    document_ids: list[str]


class UnlockBody(BaseModel):
    password: str


class RelocateBody(BaseModel):
    path: str
    confirmed_new_version: bool = False


class PageBody(BaseModel):
    page: int


class CloudPageBody(PageBody):
    """The page, and the model and price its cost was shown at: the request goes at
    those, or not at all."""

    model: str
    input_usd: float
    output_usd: float
    revision_id: str  # the document's revision as it was shown


PAGE_COST_CHANGED = (
    "The page model or its price has changed since its cost was shown. Check the new "
    "cost, then send the page again."
)


class Delivered(BaseModel):
    job_id: str
    delivered: bool


def _card_or_none(document_id: str) -> dict[str, Any] | None:
    try:
        return cards.get(document_id)
    except refusals.Refused:
        return None


@router.get("/documents/{document_id}", operation_id="get_document")
def get_document(document_id: str, _: AppToken) -> DocumentDetail:
    """A document with its card, locations, collections and how it opens."""
    # Asked first: no live work now means anything that was publishing has published,
    # so the document read next can't be older than a "finished" answer.
    has_live_work = jobs.document_has_live_work(document_id)
    document = documents.get(document_id)
    return DocumentDetail(
        document=document,
        card=_card_or_none(document_id),
        locations=documents.locations(document_id),
        collections=collections.of_document(document_id),
        view_state=files.view_state(document),
        cloud_page_allowed=policy.page_image_refusal(document_id) is None,
        has_live_work=has_live_work,
    )


def _sandboxed(data: bytes, media_type: str, attachment: str | None) -> Response:
    headers = {
        "Content-Security-Policy": files.SANDBOX,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    if attachment is not None:
        safe = attachment.replace('"', "").encode("ascii", "ignore").decode()
        headers["Content-Disposition"] = f'attachment; filename="{safe}"'
    return Response(data, media_type=media_type, headers=headers)


@router.get("/documents/{document_id}/file", operation_id="get_document_file")
def get_document_file(document_id: str, _: AppToken) -> Response:
    """The verified original, sandboxed. HTML, SVG and XML go only as attachments."""
    served = files.serve(document_id)
    return _sandboxed(
        served.data, served.media_type, served.file_name if served.attachment else None
    )


@router.get("/documents/{document_id}/preview", operation_id="get_document_preview")
def get_document_preview(document_id: str, _: AppToken) -> Response:
    """A JPEG of a HEIC, which the WebView can't show."""
    return _sandboxed(files.preview(document_id), "image/jpeg", None)


@router.get("/documents/{document_id}/text", operation_id="get_document_text")
def get_document_text(
    document_id: str,
    _: AppToken,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1)] = 500,
) -> StoredText:
    """A window of the stored text, each element with its locator."""
    return StoredText(**elements.of_document(document_id, offset, limit))


@router.get("/documents/{document_id}/related", operation_id="get_related_documents")
def get_related_documents(document_id: str, _: AppToken) -> list[dict[str, Any]]:
    """Found from their cards. A Standard document's list shows no Private one."""
    document = documents.get(document_id)
    levels = policy.scope_sensitivities(document["sensitivity"] == states.PRIVATE)
    return cards.related(document_id, levels)


@router.get("/documents/{document_id}/sheets", operation_id="get_document_sheets")
def get_document_sheets(document_id: str, _: AppToken) -> list[dict[str, Any]]:
    """A spreadsheet's sheets, and where each one's rows start."""
    return elements.sheets(document_id)


@router.get("/documents/{document_id}/pages/{page}", operation_id="get_document_page")
def get_document_page(document_id: str, page: int, _: AppToken) -> StoredText:
    """One page's or slide's stored text."""
    found = elements.of_page(document_id, page)
    return StoredText(elements=found, total=len(found))


@router.get(
    "/documents/{document_id}/elements/{element_id}", operation_id="get_cited_element"
)
def get_cited_element(document_id: str, element_id: str, _: AppToken) -> CitedElement:
    """A cited element, and whether it is on the active revision."""
    return CitedElement(**elements.cited(document_id, element_id))


@router.post("/documents/{document_id}/opened", operation_id="mark_document_opened")
def mark_document_opened(document_id: str, _: AppToken) -> Empty:
    """Records the open, for Recents."""
    documents.mark_opened(document_id)
    return Empty()


@router.put("/documents/{document_id}/star", operation_id="set_document_star")
def set_document_star(document_id: str, body: StarBody, _: AppToken) -> Empty:
    """Stars or unstars a document."""
    documents.set_starred(document_id, body.starred)
    return Empty()


@router.patch("/documents/{document_id}/card", operation_id="edit_document_card")
def edit_document_card(
    document_id: str, body: dict[str, Any], _: AppToken
) -> dict[str, Any]:
    """Saves the owner's corrections, which survive re-indexing."""
    return cards.edit_card(document_id, body)


@router.post("/documents/remove", operation_id="remove_documents")
def remove_documents(body: RemoveBody, _: AppToken) -> Empty:
    """Takes documents out of the library. No file on the Mac is touched."""
    documents.remove_from_library(body.document_ids)
    return Empty()


@router.post("/documents/{document_id}/reindex", operation_id="reindex_document")
def reindex_document(document_id: str, _: AppToken) -> Queued:
    """Indexes the document again; notes and chats stay linked."""
    return Queued(job_id=documents.reindex(document_id))


@router.post("/documents/{document_id}/unlock", operation_id="unlock_document")
async def unlock_document(
    document_id: str, body: UnlockBody, request: Request, _: AppToken
) -> Delivered:
    """Queues the job and hands the password on, once, to the worker's stdin."""
    job_id = documents.unlock_and_index(document_id)
    delivered = await request.app.state.supervisor.forward_unlock(job_id, body.password)
    return Delivered(job_id=job_id, delivered=delivered)


@router.post("/documents/{document_id}/download", operation_id="download_document")
def download_document(document_id: str, _: AppToken) -> Queued:
    """Downloads a file that's only in the cloud, then indexes it."""
    return Queued(job_id=documents.download_and_index(document_id))


@router.post("/documents/{document_id}/relocate", operation_id="relocate_document")
def relocate_document(document_id: str, body: RelocateBody, _: AppToken) -> Empty:
    """Points a missing document at where its file went, if the hash matches."""
    documents.relocate(document_id, body.path, body.confirmed_new_version)
    return Empty()


@router.post(
    "/documents/{document_id}/trash-managed-copy", operation_id="trash_managed_copy"
)
def trash_managed_copy(document_id: str, _: AppToken) -> Empty:
    """Moves the copy SecureFolio made to the Trash."""
    documents.trash_managed_copy(document_id)
    return Empty()


@router.post("/documents/{document_id}/reread-page", operation_id="reread_page")
def reread_page(document_id: str, body: PageBody, _: AppToken) -> Queued:
    """Reads this page again on this Mac."""
    return Queued(
        job_id=documents.queue_page_reading(document_id, "reread_page", body.page)
    )


@router.get(
    "/documents/{document_id}/chunks/{chunk_id}", operation_id="get_chunk_element"
)
def get_chunk_element(document_id: str, chunk_id: str, _: AppToken) -> dict[str, Any]:
    """The element a passage link opens at, or none if the passage has gone."""
    return {"element_id": elements.first_of_chunk(document_id, chunk_id)}


@router.get("/documents/{document_id}/citation", operation_id="resolve_citation")
def resolve_citation(
    document_id: str, revision_id: str, element_id: str, _: AppToken
) -> dict[str, Any]:
    """Where a citation opens now, or its stored text if the file has changed."""
    return elements.resolve(document_id, revision_id, element_id)


@router.get(
    "/documents/{document_id}/cloud-read-page", operation_id="get_cloud_page_cost"
)
def get_cloud_page_cost(document_id: str, _: AppToken) -> PageCost:
    """What reading one page in the cloud costs, or why it's unavailable now."""
    _refuse_page(document_id)
    # The model and its price, read once: the amount, the rates and whether it's
    # available all come from them.
    model = spend.page_model()
    price = model_catalog.price(model)
    return PageCost(
        model=model,
        model_name=policy.model_name(model),
        provider=providers.of(model).name,
        input_usd=price[0] if price else None,
        output_usd=price[1] if price else None,
        cost_usd=spend.page_read_estimate(price) if price else None,
        unavailable=policy.page_cloud_problem(model, price),
    )


@router.post("/documents/{document_id}/cloud-read-page", operation_id="cloud_read_page")
def cloud_read_page(document_id: str, body: CloudPageBody, _: AppToken) -> Queued:
    """Sends only this page's image, at the model and price its cost was shown at."""
    # They go with the job, so a change in Settings since can't send it at another.
    _refuse_page(document_id)
    model = spend.page_model()
    price = model_catalog.price(model)
    problem = policy.page_cloud_problem(model, price)
    if problem is not None:
        raise refusals.conflict(problem)
    if body.model != model or price != (body.input_usd, body.output_usd):
        raise refusals.conflict(PAGE_COST_CHANGED)
    return Queued(
        job_id=documents.queue_page_reading(
            document_id,
            "claude_page",
            body.page,
            {"model": model, "price": price},
            body.revision_id,
        )
    )


def _refuse_page(document_id: str) -> None:
    reason = policy.page_image_refusal(document_id)
    if reason is not None:
        raise refusals.conflict(reason)
