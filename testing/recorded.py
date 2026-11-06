"""Stands in for Docling's model-backed conversion with recorded output, so the
checks parse the fixtures with no model."""

import json
from pathlib import Path
from types import SimpleNamespace

from docling_core.types.doc.document import DoclingDocument

from data.build.record_docling import RECORDED, key_of


def convert(
    _name: str, data: bytes, page_range: tuple[int, int] | None
) -> SimpleNamespace:
    path = Path(RECORDED) / f"{key_of(data, page_range)}.json"
    document = DoclingDocument.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )
    return SimpleNamespace(document=document, confidence=None, pages=[])
