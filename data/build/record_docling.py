"""Records Docling's output for the fixtures that need its models, so the checks can
parse them with no model at hand. Run on a Mac with the models:
uv run python -m data.build.record_docling"""

import hashlib
import io
import json
from pathlib import Path

import pypdfium2
from docling.datamodel.base_models import DocumentStream
from PIL import Image

from worker import born_digital, docling_engine, parse

ROOT = Path(__file__).parent.parent
RECORDED = ROOT / "recorded"
LIBRARY = ROOT / "library"
IMAGE_SUFFIXES = {".png", ".jpeg", ".heic", ".webp"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def key_of(data: bytes, page_range: tuple[int, int] | None) -> str:
    """A picture is keyed by its pixels, not its PNG bytes: Pillow's zlib compresses
    the same pixels differently on macOS and Linux."""
    if data.startswith(PNG_SIGNATURE):
        with Image.open(io.BytesIO(data)) as picture:
            data = f"{picture.mode} {picture.size}".encode() + picture.tobytes()
    return hashlib.sha256(data + repr(page_range).encode()).hexdigest()


def record(name: str, data: bytes, page_range: tuple[int, int] | None) -> None:
    arguments = {"page_range": page_range} if page_range else {}
    source = DocumentStream(name=name, stream=io.BytesIO(data))
    result = docling_engine.full_converter().convert(source, **arguments)
    path = RECORDED / f"{key_of(data, page_range)}.json"
    path.write_text(json.dumps(result.document.export_to_dict()), encoding="utf-8")


def main() -> None:
    RECORDED.mkdir(exist_ok=True)
    for path in sorted(LIBRARY.rglob("*")):
        data = path.read_bytes() if path.is_file() else b""
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            png = parse._to_png(data, parse.KIND_OF_SUFFIX[suffix])
            record(path.with_suffix(".png").name, png, None)
        elif suffix == ".pdf" and data and "Locked" not in path.name:
            source = pypdfium2.PdfDocument(data)
            total = len(source)
            simple = {
                n
                for n in range(1, total + 1)
                if born_digital.is_simple_page(source[n - 1])
            }
            hard = [n for n in range(1, total + 1) if n not in simple]
            for run in parse._page_runs(hard):
                record(path.name, data, run)
            print(path.name, "simple", len(simple), "hard", len(hard), flush=True)


if __name__ == "__main__":
    main()
