"""The worker the end-to-end tests run: recorded Docling output and stand-ins for the
OCR and page-reading models, since the checks call no real model."""

import time
from pathlib import Path

from docling_core.types.doc.document import DoclingDocument

from backend import embedder
from testing import recorded
from worker import main as worker_main
from worker import ocr, page_reader, parse

SLOW_FLAG = Path("e2e/.fixtures/slow")
SLOW_SECONDS = 0.5


def _slowed(embed):
    """Holds each document back while a test needs jobs to stay queued."""

    def run(texts):
        if SLOW_FLAG.exists():
            time.sleep(SLOW_SECONDS)
        return embed(texts)

    return run


def use_stand_ins() -> None:
    embedder.embed_documents = _slowed(embedder.embed_documents)
    parse.use_recorded(recorded.convert)
    ocr.use_stand_in(lambda _image: [])
    page_reader.use_stand_in(lambda _pdf, _page: DoclingDocument(name="empty"))


if __name__ == "__main__":
    use_stand_ins()
    worker_main.main()
