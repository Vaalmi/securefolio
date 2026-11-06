"""Watched folders, and the built-in source for files copied in."""

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from backend import refusals, sources
from backend.dependencies import AppToken

router = APIRouter()


class OrganizeEstimate(BaseModel):
    backfill_usd: float | None  # None: the helper model has no known price
    per_document_usd: float | None
    batched: bool
    model: str  # the helper model that would write the cards
    model_name: str
    provider: str | None  # its provider's name; None for the local model
    input_usd: float | None  # its full price per million tokens; None: none known
    output_usd: float | None
    backfill_input_usd: float | None  # the backfill's: the batch price when batched
    backfill_output_usd: float | None
    key_ready: bool  # whether cloud organizing can be turned on
    unavailable: str | None  # why the helper can't write them now: no key, say


class Empty(BaseModel):
    pass


class OrganizeCost(BaseModel):
    """Turning organizing on carries the helper model and price its estimate showed."""

    model: str | None = None
    input_usd: float | None = None
    output_usd: float | None = None
    batched: bool | None = None  # whether the estimate was at the batch price


class AddSource(OrganizeCost):
    path: str
    sensitivity: Literal["standard", "private"]
    auto_organize: bool = False
    mcp_enabled: bool = True


class SourcePatch(OrganizeCost):
    sensitivity: Literal["standard", "private"] | None = None
    auto_organize: bool | None = None
    mcp_enabled: bool | None = None


def _refuse_organizing(
    turning_on: bool | None, shown: OrganizeCost, source_id: str | None = None
) -> None:
    """Cloud organizing goes on only with the helper's provider key ready, and at the
    helper, price and batch price its estimate showed; turning it off, or leaving it
    on, never asks. A new folder has no estimate of its own to bind."""
    if not turning_on:
        return
    shown_for = (source_id, shown.batched) if source_id is not None else None
    refused = sources.organize_refusal() or sources.organize_cost_refusal(
        shown.model, shown.input_usd, shown.output_usd, shown_for
    )
    if refused is not None:
        raise refusals.conflict(refused)


@router.get("/sources", operation_id="list_sources")
def list_sources(_: AppToken) -> list[dict[str, Any]]:
    """Every live source, with how many documents it holds."""
    return sources.live_with_counts()


@router.post("/sources", operation_id="add_source")
def add_source(body: AddSource, _: AppToken) -> dict[str, Any]:
    """Starts watching a folder and queues its first scan."""
    _refuse_organizing(body.auto_organize, body)
    return sources.add_source(
        body.path, body.sensitivity, body.auto_organize, body.mcp_enabled
    )


@router.patch("/sources/{source_id}", operation_id="update_source")
def update_source(source_id: str, body: SourcePatch, _: AppToken) -> Empty:
    """Changes a folder's sensitivity or its switches."""
    _refuse_organizing(
        body.auto_organize and not sources.get(source_id)["auto_organize"],
        body,
        source_id,
    )
    if body.sensitivity is not None:
        sources.set_sensitivity(source_id, body.sensitivity)
    sources.set_switches(source_id, body.auto_organize, body.mcp_enabled)
    return Empty()


@router.get(
    "/sources/{source_id}/organize-estimate", operation_id="get_organize_estimate"
)
def get_organize_estimate(source_id: str, _: AppToken) -> OrganizeEstimate:
    """What letting the helper model write this folder's cards would cost."""
    return OrganizeEstimate(**sources.organize_estimate(source_id))


@router.post("/sources/{source_id}/rescan", operation_id="rescan_source")
def rescan_source(source_id: str, _: AppToken) -> Empty:
    """Scans the folder again now."""
    sources.rescan(source_id)
    return Empty()


@router.post("/sources/{source_id}/pause", operation_id="pause_source")
def pause_source(source_id: str, _: AppToken) -> Empty:
    """Stops watching for now, keeping the documents."""
    sources.pause(source_id)
    return Empty()


@router.post("/sources/{source_id}/resume", operation_id="resume_source")
def resume_source(source_id: str, _: AppToken) -> Empty:
    """Starts watching again."""
    sources.resume(source_id)
    return Empty()


@router.delete("/sources/{source_id}", operation_id="stop_watching_source")
def stop_watching_source(source_id: str, _: AppToken) -> Empty:
    """Its documents leave the library; the files stay where they are."""
    sources.stop_watching(source_id)
    return Empty()
