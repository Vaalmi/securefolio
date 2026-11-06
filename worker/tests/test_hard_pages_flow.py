"""The second reading: after publish, only when it changes something, with grounding
that shows an area rather than guess a highlight."""

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

from agent import models
from backend import (
    database,
    documents,
    embedder,
    jobs,
    revisions,
    settings,
    sources,
    states,
)
from testing.queue import drain, next_job
from testing.stub_models import stub_ollama
from worker import hard_pages, ocr, page_reader, parse
from worker.content import Element
from worker.hard_pages import Thresholds
from worker.ocr import Word

DATA = Path(__file__).parents[2] / "data" / "library"


def words(*texts: str, y: float = 0.1) -> list[Word]:
    return [
        Word(t, 1.0, (0.1 + i * 0.1, y, 0.19 + i * 0.1, y + 0.02))
        for i, t in enumerate(texts)
    ]


def read_as(text: str):
    """A page reader that finds this text, with a box, on the page asked for."""

    def read(_pdf, page):
        document = DoclingDocument(name="read")
        document.add_page(page_no=page, size=Size(width=100, height=100))
        box = BoundingBox(l=5, t=5, r=95, b=20, coord_origin=CoordOrigin.TOPLEFT)
        prov = ProvenanceItem(page_no=page, bbox=box, charspan=(0, len(text)))
        document.add_text(label=DocItemLabel.TEXT, text=text, prov=prov)
        return document

    return read


def only_document():
    with database.read() as connection:
        return dict(connection.execute(sa.select(database.documents)).mappings().one())


@pytest.fixture
def scanned(library, tmp_path):
    folder = tmp_path / "scans"
    folder.mkdir()
    shutil.copy(DATA / "Scans" / "Scanned account letter.pdf", folder / "letter.pdf")
    sources.add_source(str(folder), states.STANDARD)
    return folder


def run_until_published() -> None:
    """Everything up to the hard-pages job."""
    while True:
        job = jobs.claim((jobs.SCAN, jobs.INGEST))
        if job is None:
            return
        from worker import main as worker_main  # noqa: PLC0415

        worker_main.run_job(job)


def test_a_document_is_searchable_before_its_hard_pages_run(scanned):
    run_until_published()
    document = only_document()
    assert document["status"] == states.INDEXED
    hard = next_job((jobs.HARD_PAGES,))
    assert hard is not None
    assert hard["priority"] > jobs.PRIORITY_BULK


def test_a_bad_text_layer_is_read_again_into_a_new_revision(scanned):
    ocr.use_stand_in(
        lambda _image: words("the", "late", "fee", "is", "due", "monthly", "here")
    )
    page_reader.use_stand_in(read_as("the late fee is due monthly here"))
    run_until_published()
    first = only_document()["active_revision_id"]
    drain()
    after = only_document()
    assert after["active_revision_id"] != first
    assert after["status"] == states.INDEXED
    with database.read() as connection:
        text = connection.execute(sa.select(database.chunks.c.text)).scalars().all()
    assert "the late fee is due monthly here" in " ".join(text)


def test_a_reading_that_changes_nothing_publishes_nothing(library, tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    shutil.copy(DATA / "Home" / "Plain text.pdf", folder / "plain.pdf")
    sources.add_source(str(folder), states.STANDARD)
    ocr.use_stand_in(lambda _image: [])
    run_until_published()
    first = only_document()["active_revision_id"]
    drain()
    assert only_document()["active_revision_id"] == first


def test_the_layout_and_embedder_unload_before_the_reader_loads(scanned, monkeypatch):
    calls = []
    ocr.use_stand_in(lambda _image: words("new", "text", "here"))

    def reader(pdf, page):
        calls.append("read")
        return read_as("new text here")(pdf, page)

    page_reader.use_stand_in(reader)
    real_unload = page_reader.unload
    monkeypatch.setattr(embedder, "unload", lambda: calls.append("embedder off"))
    monkeypatch.setattr(parse, "unload_models", lambda: calls.append("layout off"))
    monkeypatch.setattr(
        page_reader, "unload", lambda: (calls.append("reader off"), real_unload())
    )
    monkeypatch.setattr(
        embedder,
        "embed_documents",
        lambda texts: (calls.append("embed"), embedder._stand_in(texts))[1],
    )
    run_until_published()
    calls.clear()
    drain()
    assert calls.index("embedder off") < calls.index("read")
    assert calls.index("layout off") < calls.index("read")
    assert calls.index("read") < calls.index("reader off") < calls.index("embed")


class TestGrounding:
    def test_words_inside_the_box_that_match_are_a_highlight(self):
        vision = words("the", "late", "fee")
        placed: Any = hard_pages.ground_in_box("The late fee", (0, 0, 1, 1), vision)
        assert placed.precision == "words"

    def test_words_that_dont_match_are_not(self):
        vision = words("reordered", "cells", "here")
        assert (
            hard_pages.ground_in_box("cells here reordered", (0, 0, 1, 1), vision)
            is None
        )
        assert hard_pages.ground_in_box("anything", (0.9, 0.9, 1, 1), vision) is None

    def test_a_run_of_five_words_found_once_places_text_with_no_box(self):
        vision = words("one", "two", "three", "four", "five", "six")
        placed: Any = hard_pages.ground_without_box("one two three four five", vision)
        assert placed.precision == "words"

    def test_a_short_run_or_a_repeated_phrase_doesnt(self):
        vision = words(
            "same",
            "words",
            "again",
            "here",
            "now",
            "same",
            "words",
            "again",
            "here",
            "now",
        )
        assert (
            hard_pages.ground_without_box("same words again here now", vision) is None
        )
        assert hard_pages.ground_without_box("same words", vision) is None

    def test_an_element_that_cant_be_grounded_cites_its_box_or_the_page(self):
        from backend.locators import PdfLocator, Rect  # noqa: PLC0415

        box = Rect(x0=0.1, y0=0.1, x1=0.5, y1=0.2)
        with_box: Any = Element(
            "text", "not there", PdfLocator(page=3, rects=[box], precision="element")
        )
        hard_pages.place(with_box, 3, words("other"))
        assert with_box.locator.precision == "element"
        assert with_box.locator.rects == [box]
        no_box: Any = Element(
            "text", "not there either", PdfLocator(page=3, rects=[], precision="page")
        )
        hard_pages.place(no_box, 3, words("other"))
        assert no_box.locator.precision == "page"
        table: Any = Element(
            "table", "| a |", PdfLocator(page=3, rects=[box], precision="element")
        )
        hard_pages.place(table, 3, words("a"))
        assert table.locator.precision == "element"


def test_a_table_across_a_page_break_takes_the_first_header_and_keeps_its_page():
    from backend.locators import PdfLocator, Rect  # noqa: PLC0415

    box = Rect(x0=0, y0=0, x1=1, y1=1)
    first = Element(
        "table",
        "| A | B |\n| --- | --- |\n| 1 | 2 |",
        PdfLocator(page=1, rects=[box], precision="element"),
    )
    second: Any = Element(
        "table",
        "| 3 | 4 |\n| 5 | 6 |",
        PdfLocator(page=2, rects=[box], precision="element"),
    )
    hard_pages.stitch_tables([first, second], {1})
    assert second.text.startswith("| A | B |")
    assert second.locator.page == 2


def chart_page(library, tmp_path):
    folder = tmp_path / "charts"
    folder.mkdir()
    shutil.copy(DATA / "Home" / "Water use chart.pdf", folder / "chart.pdf")
    sources.add_source(str(folder), states.STANDARD)


def test_a_picture_with_no_reader_is_pending_and_read_once_there_is_one(
    library, tmp_path, monkeypatch
):
    chart_page(library, tmp_path)
    monkeypatch.setattr(models, "local_ready", lambda *_given: False)
    monkeypatch.setattr(models, "tag_listed", lambda *_given: False)
    run_until_published()
    drain()
    with database.read() as connection:
        states_seen = (
            connection.execute(
                sa.select(database.elements.c.reading_state).where(
                    database.elements.c.kind == "picture"
                )
            )
            .scalars()
            .all()
        )
    assert states_seen
    assert set(states_seen) == {states.PENDING}
    # The reader arrives; the backend queues the waiting documents again.
    monkeypatch.setattr(models, "local_ready", lambda *_given: True)
    monkeypatch.setattr(models, "tag_listed", lambda *_given: True)
    monkeypatch.setattr(
        models,
        "read_picture",
        lambda _image, _tag=None: {"kind": "chart", "table": [["Jan", 40]]},
    )
    assert hard_pages.queue_pending_when_ready() == 1
    drain()
    with database.read() as connection:
        rows = (
            connection.execute(
                sa.select(database.elements).where(
                    database.elements.c.kind == "picture"
                )
            )
            .mappings()
            .all()
        )
    current = only_document()["active_revision_id"]
    active = [r for r in rows if r["revision_id"] == current]
    assert active[0]["reading_state"] == states.READ
    assert "| Jan | 40 |" in active[0]["text"]
    assert active[0]["locator"]["precision"] == "element"


def test_the_local_model_is_asked_only_on_loopback_and_for_a_listed_tag(
    library, monkeypatch
):
    with stub_ollama() as stub:
        monkeypatch.setenv("OLLAMA_HOST", stub.url)
        assert models.local_ready()
        monkeypatch.setenv("LOCAL_MODEL", "not-installed:1b")
        assert not models.local_ready()
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")
    assert not models.local_ready()


def test_a_stale_reading_is_discarded_and_read_again(scanned, monkeypatch):
    ocr.use_stand_in(lambda _image: words("other", "words"))
    page_reader.use_stand_in(read_as("fresh reading"))
    run_until_published()
    job = next_job((jobs.HARD_PAGES,))
    document = documents.get(job["document_id"])
    hard_pages._publish_changes(job, document, [], "an-older-revision")
    with database.read() as connection:
        again = connection.execute(
            sa.select(database.jobs).where(
                database.jobs.c.idempotency_key.like("hard:%:again")
            )
        ).first()
    assert again is not None


def test_thresholds_are_the_recorded_ones():
    assert Thresholds().character_error_rate == 0.10
    assert Thresholds().coverage == 0.90


def test_an_image_gets_its_pictures_read_without_page_routing(
    library, tmp_path, monkeypatch
):
    folder = tmp_path / "images"
    folder.mkdir()
    shutil.copy(DATA / "Scans" / "Screenshot.png", folder / "shot.png")
    sources.add_source(str(folder), states.STANDARD)
    monkeypatch.setattr(models, "local_ready", lambda *_given: False)
    drain()
    assert only_document()["status"] == states.INDEXED


def test_rereading_a_picture_reads_it_and_publishes_the_text(library, tmp_path):
    folder = tmp_path / "pictures"
    folder.mkdir()
    shutil.copy(DATA / "Scans" / "Screenshot.png", folder / "Screenshot.png")
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        document_id = connection.execute(
            sa.select(database.documents.c.id)
        ).scalar_one()
    page_reader.use_stand_in(read_as("the picture read again"))
    documents.queue_page_reading(document_id, "reread_page", 1)
    drain()
    assert "the picture read again" in " ".join(
        e["text"] for e in revisions.active_elements(document_id)
    )


def test_a_page_docling_fails_to_read_again_is_none_not_an_error():
    from docling.datamodel.base_models import ConversionStatus  # noqa: PLC0415

    from worker import docling_engine  # noqa: PLC0415

    asked = []

    class Converter:
        def __init__(self, status):
            self.status = status

        def convert(self, _source, **options):
            asked.append(options)
            return SimpleNamespace(status=self.status, document="the page")

    failed = Converter(ConversionStatus.FAILURE)
    assert docling_engine.read_or_none(failed, "page.pdf", 3) is None  # type: ignore[arg-type]
    read = Converter(ConversionStatus.SUCCESS)
    assert docling_engine.read_or_none(read, "page.pdf", 3) == "the page"  # type: ignore[arg-type]
    assert asked[0] == {"page_range": (3, 3), "raises_on_error": False}


def last_hard_pages_job() -> dict[str, Any]:
    with database.read() as connection:
        job = (
            connection.execute(
                sa.select(database.jobs)
                .where(database.jobs.c.kind == jobs.HARD_PAGES)
                .order_by(database.jobs.c.created_at.desc())
            )
            .mappings()
            .first()
        )
    assert job is not None
    return dict(job)


def test_a_hard_page_the_reader_fails_on_keeps_its_reading_and_the_job_goes_on(
    scanned,
):
    ocr.use_stand_in(lambda _image: words("the", "late", "fee", "is", "due"))
    asked = []
    page_reader.use_stand_in(lambda _pdf, page: asked.append(page))
    run_until_published()
    first = only_document()["active_revision_id"]
    drain()
    assert asked == [1]
    assert last_hard_pages_job()["state"] == states.SUCCEEDED
    assert only_document()["active_revision_id"] == first


def test_a_page_the_owner_asked_about_that_fails_is_a_failure_to_retry(
    library, tmp_path
):
    folder = tmp_path / "pictures"
    folder.mkdir()
    shutil.copy(DATA / "Scans" / "Screenshot.png", folder / "Screenshot.png")
    sources.add_source(str(folder), states.STANDARD)
    drain()
    document_id = only_document()["id"]
    before = [e["text"] for e in revisions.active_elements(document_id)]
    page_reader.use_stand_in(lambda _pdf, _page: None)
    documents.queue_page_reading(document_id, "reread_page", 1)
    drain()
    failed = last_hard_pages_job()
    assert failed["state"] == states.FAILED
    assert failed["reason"] == hard_pages.PAGE_UNREAD
    assert [e["text"] for e in revisions.active_elements(document_id)] == before
    page_reader.use_stand_in(read_as("the picture read again"))
    documents.retry_job(failed["id"])
    drain()
    assert jobs.get(failed["id"])["state"] == states.SUCCEEDED
    assert "the picture read again" in " ".join(
        e["text"] for e in revisions.active_elements(document_id)
    )


def pending_chart(library, tmp_path, monkeypatch) -> None:
    """A chart page whose picture waited for a figures model, then one is there."""
    chart_page(library, tmp_path)
    monkeypatch.setattr(models, "local_ready", lambda *_given: False)
    monkeypatch.setattr(models, "tag_listed", lambda *_given: False)
    run_until_published()
    drain()
    monkeypatch.setattr(models, "local_ready", lambda *_given: True)
    monkeypatch.setattr(models, "tag_listed", lambda *_given: True)


def test_requeued_pictures_redo_no_page_read_before(library, tmp_path, monkeypatch):
    # Only the waiting pictures are read.
    pending_chart(library, tmp_path, monkeypatch)
    monkeypatch.setattr(
        models,
        "read_picture",
        lambda _image, _tag=None: {"kind": "chart", "table": [["Jan", 40]]},
    )
    pages_read = []
    real = hard_pages._read_page_if_hard  # noqa: SLF001

    def counted(*given, **named):
        pages_read.append(given[1])
        return real(*given, **named)

    monkeypatch.setattr(hard_pages, "_read_page_if_hard", counted)
    assert hard_pages.queue_pending_when_ready() == 1
    drain()
    assert pages_read == [], "no page is read again"
    with database.read() as connection:
        read = (
            connection.execute(
                sa.select(database.elements.c.reading_state).where(
                    database.elements.c.kind == "picture",
                    database.elements.c.revision_id
                    == only_document()["active_revision_id"],
                )
            )
            .scalars()
            .all()
        )
    assert read == [states.READ]


def test_a_figures_model_chosen_as_the_job_runs_is_the_next_jobs(
    library, tmp_path, monkeypatch
):
    # The tag is read as the job starts.
    pending_chart(library, tmp_path, monkeypatch)
    started_on = settings.get(settings.LOCAL_FIGURES)
    tags = []

    def read(_image, tag=None):
        tags.append(tag)
        return {"kind": "chart", "table": [["Jan", 40]]}

    real = hard_pages._render  # noqa: SLF001

    def changed_while_rendering(*given, **named):
        settings.put(settings.LOCAL_FIGURES, "gemma4:e4b")  # the owner, meanwhile
        return real(*given, **named)

    monkeypatch.setattr(models, "read_picture", read)
    monkeypatch.setattr(hard_pages, "_render", changed_while_rendering)
    assert hard_pages.queue_pending_when_ready() == 1
    drain()
    assert tags == [started_on]
