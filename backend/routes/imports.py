"""Files and folders dropped on the app."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from backend import documents, settings
from backend.dependencies import AppToken

router = APIRouter()


class ImportRequest(BaseModel):
    paths: list[str]
    sensitivity: Literal["standard", "private"] | None = None


class Imported(BaseModel):
    document_id: str
    job_id: str


@router.post("/imports", operation_id="import_paths")
def import_paths(body: ImportRequest, _: AppToken) -> list[Imported]:
    """Copies files in, each with its own job; a file not on this Mac is only listed."""
    return [
        Imported(**r)
        for r in documents.import_paths(
            body.paths, body.sensitivity or settings.get(settings.DEFAULT_SENSITIVITY)
        )
    ]
