"""The born-digital path against Docling's full pipeline, on the single-column
fixtures: the same words on the page, and how much faster.
Run from the repo root, as a module: uv run -m scripts.measure.born_digital"""

import io
import json
import time
from pathlib import Path

from backend import telemetry

# Before Docling is imported: Hugging Face reads its telemetry switch then.
telemetry.turn_off()

import pypdfium2  # noqa: E402
from docling.datamodel.base_models import DocumentStream  # noqa: E402
from docling.document_converter import DocumentConverter  # noqa: E402
from rapidfuzz.distance import Levenshtein  # noqa: E402

from worker import born_digital  # noqa: E402
from worker.hard_pages import normalize  # noqa: E402

ROOT = (
    Path(__file__).parent.parent.parent
    / "data"
    / "evals"
    / "calibration"
    / "hard_pages"
)


MIN_OVERLAP = 0.5


def elements_of(document) -> list[tuple[str, str]]:
    items = document.iterate_items()
    return [
        (str(item.label), normalize(item.text))
        for item, _ in items
        if hasattr(item, "text") and item.text.strip()
    ]


def boxes_agree(quick, full) -> bool:
    """Each element's page matches, and its box overlaps the full pipeline's."""
    pairs = zip(quick.iterate_items(), full.iterate_items(), strict=False)
    for (a, _), (b, _) in pairs:
        if not (getattr(a, "prov", None) and getattr(b, "prov", None)):
            continue
        one, other = a.prov[0], b.prov[0]
        box = one.bbox.to_bottom_left_origin(800)
        far = other.bbox.to_bottom_left_origin(800)
        if (
            one.page_no != other.page_no
            or box.intersection_over_union(far) < MIN_OVERLAP
        ):
            return False
    return True


def text_of(document) -> str:
    return normalize(
        " ".join(
            item.text for item, _ in document.iterate_items() if hasattr(item, "text")
        )
    )


def main() -> None:
    converter = DocumentConverter()
    simple = complex_pages = 0
    distances = []
    same_elements = same_boxes = 0
    fast = full = 0.0
    for path in sorted((ROOT / "tuning").glob("good_*.pdf")):
        data = path.read_bytes()
        if not born_digital.is_simple_page(pypdfium2.PdfDocument(data)[0]):
            complex_pages += 1
            continue
        simple += 1
        begun = time.time()
        quick = born_digital.convert(data, path.name)
        fast += time.time() - begun
        begun = time.time()
        converted = converter.convert(
            DocumentStream(name=path.name, stream=io.BytesIO(data))
        )
        full += time.time() - begun
        same_elements += elements_of(quick) == elements_of(converted.document)
        same_boxes += boxes_agree(quick, converted.document)
        distances.append(
            Levenshtein.normalized_distance(text_of(quick), text_of(converted.document))
        )
    print(
        json.dumps(
            {
                "simple": simple,
                "same_elements": same_elements,
                "same_boxes": same_boxes,
                "not_simple": complex_pages,
                "worst_text_difference": max(distances),
                "fast_seconds": round(fast, 2),
                "full_seconds": round(full, 2),
            }
        )
    )


if __name__ == "__main__":
    main()
