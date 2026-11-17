"""A citation opens at its sentence's figures: placed only where each is found once
inside the cited element, by pdfium's boxes on a PDF page with a text layer or by
characters in a text kind, and never guessed."""

import io
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pypdfium2
import pytest
import sqlalchemy as sa

from agent import figures as agent_figures
from backend import (
    database,
    elements,
    figure_places,
    files,
    keychain,
    sources,
    states,
)
from backend.build_mode import STUB_ANTHROPIC_ENV
from backend.figure_places import Shown
from testing.ask_helpers import (
    ask,
    last_answer,
    message,
    new_thread,
    plan_reply,
    system_of,
)
from testing.queue import drain
from testing.stub_models import stub_anthropic

DATA = Path(__file__).parents[2] / "data" / "library"
COST = "$14,325.35"  # the roof report's estimate
WHOLE_PAGE = {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}


@pytest.fixture
def home(library, tmp_path):
    root = tmp_path / "Home"
    root.mkdir()
    names = (
        "Roof inspection report.pdf",
        "Rotated page.pdf",
        "Cropped page.pdf",
        "Water use chart.pdf",
    )
    for name in names:
        shutil.copy(DATA / "Home" / name, root / name)
    sources.add_source(str(root), states.STANDARD)
    drain()


def holding_kind(title: str, kind: str) -> tuple[str, dict]:
    """A document's first element of a kind."""
    with database.read() as connection:
        row = (
            connection.execute(
                sa.select(database.elements, database.revisions.c.document_id)
                .join(
                    database.revisions,
                    database.revisions.c.id == database.elements.c.revision_id,
                )
                .join(
                    database.documents,
                    database.documents.c.id == database.revisions.c.document_id,
                )
                .where(
                    database.documents.c.title == title,
                    database.elements.c.kind == kind,
                )
            )
            .mappings()
            .first()
        )
    assert row is not None, (title, kind)
    return row["document_id"], dict(row)


def holding(words: str) -> tuple[str, dict]:
    """The document and element whose text holds the words."""
    with database.read() as connection:
        row = (
            connection.execute(
                sa.select(database.elements, database.revisions.c.document_id)
                .join(
                    database.revisions,
                    database.revisions.c.id == database.elements.c.revision_id,
                )
                .where(database.elements.c.text.contains(words))
            )
            .mappings()
            .first()
        )
    assert row is not None, words
    return row["document_id"], dict(row)


def test_a_born_digital_pdfs_figure_is_placed_inside_its_element(home):
    document_id, element = holding(COST)
    locator = element["locator"]
    placed = figure_places.pdf_places(
        files.serve(document_id).data, locator, [Shown(COST, COST)]
    )
    assert [p["text"] for p in placed] == [COST]
    (line,) = placed[0]["rects"]
    assert figure_places._inside(line, locator["rects"])  # noqa: SLF001
    assert 0 < line["x1"] - line["x0"] < 0.5, "the figure, not its whole element"


def test_a_figure_found_twice_in_its_passage_isnt_placed():
    text = "\U0001f3e0 A fee of $50 now and $50 later, and $75 once."
    shown = [Shown("$50", "$50"), Shown("$75", "$75")]
    (once,) = figure_places.text_places(text, shown)
    # By characters, as the app cuts the text: the emoji is one.
    assert once == {"kind": "text", "text": "$75", "range": [38, 41]}
    assert "".join(list(text)[38:41]) == "$75"


def test_a_rotated_page_places_nothing(home):
    pdf = pypdfium2.PdfDocument(DATA / "Home" / "Rotated page.pdf")
    assert pdf[0].get_rotation(), "the fixture is a rotated page"
    words = pdf[0].get_textpage().get_text_range().split()[0]
    placed = figure_places.pdf_places(
        (DATA / "Home" / "Rotated page.pdf").read_bytes(),
        {"page": 1, "rects": [WHOLE_PAGE]},
        [Shown(words, words)],
    )
    assert placed == []


def test_a_scan_with_no_text_layer_places_nothing():
    scan = (DATA / "Scans" / "Scanned letter.pdf").read_bytes()
    placed = figure_places.pdf_places(
        scan, {"page": 1, "rects": [WHOLE_PAGE]}, [Shown("Dear", "Dear")]
    )
    assert placed == []


def test_a_page_whose_text_layer_is_broken_places_nothing(home):
    document_id, element = holding(COST)
    page = element["locator"]["page"]
    with database.write() as connection:
        connection.execute(
            database.page_readings.update()
            .where(database.page_readings.c.page == page)
            .values(reasons={"page": page, "broken_text_layer": True})
        )
    assert figure_places._untrusted_text_layer(document_id, page)  # noqa: SLF001


def test_a_cropped_pages_figure_is_placed_on_the_visible_page(home):
    """Pdfium counts from the whole page, the index from what the
    crop box shows; placed in the index's frame, the words fall inside their own
    passage's box."""
    pdf = pypdfium2.PdfDocument(DATA / "Home" / "Cropped page.pdf")
    crop = pdf[0].get_cropbox()
    assert crop is not None
    assert crop[:2] != (0, 0), "the fixture's crop box is offset"
    document_id, element = holding_kind("Cropped page", "text")
    assert "quartz marker" in element["text"]
    locator = element["locator"]
    (placed,) = figure_places.pdf_places(
        files.serve(document_id).data, locator, [Shown("quartz", "quartz")]
    )
    (line,) = placed["rects"]
    assert figure_places._inside(line, locator["rects"])  # noqa: SLF001


def test_an_oversized_crop_box_is_cut_to_the_page_it_shows(home):
    """A crop box larger than the media box shows only their
    intersection, so the figure is placed as on the same page uncropped."""
    document_id, element = holding(COST)
    original = files.serve(document_id).data
    pdf = pypdfium2.PdfDocument(original)
    page = pdf[element["locator"]["page"] - 1]
    media = page.get_mediabox()
    assert media is not None
    left, bottom, right, top = media
    page.set_cropbox(left - 100, bottom - 100, right + 100, top + 100)
    larger = io.BytesIO()
    pdf.save(larger)
    shown = [Shown(COST, COST)]
    as_is = figure_places.pdf_places(original, element["locator"], shown)
    oversized = figure_places.pdf_places(larger.getvalue(), element["locator"], shown)
    assert as_is
    assert oversized == as_is


def test_a_charts_element_places_nothing(home, monkeypatch):
    """A picture's words, a chart's included, describe it."""
    document_id, element = holding_kind("Water use chart", "picture")
    monkeypatch.setattr(figure_places, "shown_in", lambda *_: [Shown("$100", "$100")])

    def never(*_given):
        raise AssertionError("a picture was searched for a place")

    monkeypatch.setattr(figure_places, "pdf_places", never)
    monkeypatch.setattr(figure_places, "text_places", never)
    assert figure_places.places(document_id, element["id"], "m", 0) == []


def test_a_revision_published_while_placing_places_nothing(home, monkeypatch):
    """The element is read as current, then a re-index
    publishes a newer revision before the file is read. The newer bytes here hold
    the figure in the same box, the worst case: still, no place from two revisions."""
    document_id, element = holding(COST)
    monkeypatch.setattr(figure_places, "shown_in", lambda *_: [Shown(COST, COST)])
    placed = figure_places.places(document_id, element["id"], "m", 0)
    assert [p["text"] for p in placed] == [COST], "placed with no re-index"
    read_element = elements.cited

    def then_a_newer_revision(*given):
        cited = read_element(*given)
        with database.write() as connection:
            older = (
                connection.execute(
                    sa.select(database.revisions).where(
                        database.revisions.c.id == element["revision_id"]
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                database.revisions.insert().values({**older, "id": "newer"})
            )
            connection.execute(
                database.revisions.update()
                .where(database.revisions.c.id == older["id"])
                .values(state=states.RETIRED)
            )
            connection.execute(
                database.documents.update()
                .where(database.documents.c.id == document_id)
                .values(active_revision_id="newer")
            )
        return cited

    monkeypatch.setattr(elements, "cited", then_a_newer_revision)
    assert figure_places.places(document_id, element["id"], "m", 0) == []


def test_two_placings_at_once_never_call_pdfium_together(home, monkeypatch):
    """Two figure requests run on two threads, and PDFium
    can't be called from both at once. A document opened is closed before the next
    is: with both open together, two threads crashed the process in a trial."""
    document_id, element = holding(COST)
    data = files.serve(document_id).data
    gate = threading.Lock()
    open_now, most = [0], [0]

    class Counted(pypdfium2.PdfDocument):
        def __init__(self, *given, **named):
            with gate:
                open_now[0] += 1
                most[0] = max(most[0], open_now[0])
            time.sleep(0.05)  # room for the other thread to open one too
            super().__init__(*given, **named)

        def close(self, *given, **named):
            with gate:
                open_now[0] -= 1
            return super().close(*given, **named)

    monkeypatch.setattr(pypdfium2, "PdfDocument", Counted)
    shown = [Shown(COST, COST)]
    with ThreadPoolExecutor(2) as pool:
        both = list(
            pool.map(
                lambda _: figure_places.pdf_places(data, element["locator"], shown),
                range(2),
            )
        )
    assert [[p["text"] for p in placed] for placed in both] == [[COST], [COST]]
    assert most[0] == 1, "two documents were open in PDFium at once"


def test_a_figure_outside_the_cited_elements_box_isnt_placed(home):
    document_id, element = holding(COST)
    elsewhere = {"x0": 0.0, "y0": 0.0, "x1": 0.01, "y1": 0.01}
    placed = figure_places.pdf_places(
        files.serve(document_id).data,
        {**element["locator"], "rects": [elsewhere]},
        [Shown(COST, COST)],
    )
    assert placed == []


def citing_the_cost(_path: str, body: dict) -> dict:
    """Claude cites the block holding the roof's estimate, wherever it is."""
    system = system_of(body)
    if "search query that stands alone" in system:
        return plan_reply(body, query="roof estimated cost")
    if "Keep what a later follow-up" in system:
        return message(body, [{"type": "text", "text": "A short summary."}])
    results = [
        b
        for b in body["messages"][-1]["content"]
        if isinstance(b, dict) and b.get("type") == "search_result"
    ]
    for at, result in enumerate(results):
        for block, content in enumerate(result["content"]):
            if COST in content["text"]:
                cited = {
                    "type": "search_result_location",
                    "source": result["source"],
                    "title": result["title"],
                    "cited_text": content["text"],
                    "search_result_index": at,
                    "start_block_index": block,
                    "end_block_index": block + 1,
                }
                text = f"The roof work is estimated at {COST}."
                return message(
                    body, [{"type": "text", "text": text, "citations": [cited]}]
                )
    raise AssertionError("no passage held the estimate")


def test_the_route_places_a_saved_answers_figures_by_ids_alone(
    home, app_client, monkeypatch
):
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = citing_the_cost
        found = ask(app_client, new_thread(app_client), "What does the roof cost?")
    saved = found["messages"][-1]
    (citation,) = last_answer(found)["citations"]
    route = (
        f"/api/documents/{citation['document_id']}/elements/"
        f"{citation['element_id']}/figures"
    )
    placed = app_client.get(route, params={"message": saved["id"], "sentence": 0})
    assert placed.status_code == 200
    # The whole route, its answer, element and verified file included.
    started = time.perf_counter()
    app_client.get(route, params={"message": saved["id"], "sentence": 0})
    assert time.perf_counter() - started < 0.05
    (figure,) = placed.json()["figures"]
    assert (figure["kind"], figure["text"]) == ("pdf", COST)
    assert figure["rects"]
    other = app_client.get(route, params={"message": saved["id"], "sentence": 4})
    assert other.json() == {"figures": []}, "no such sentence"
    nobody = app_client.get(route, params={"message": "none", "sentence": 0})
    assert nobody.json() == {"figures": []}, "no such answer"


def largest_page() -> tuple[bytes, int, str]:
    """The page in the synthetic library holding the most text, and a word on it."""
    best: tuple[int, bytes, int, str] = (0, b"", 0, "")
    for path in DATA.rglob("*.pdf"):
        try:
            pdf = pypdfium2.PdfDocument(path)
        except pypdfium2.PdfiumError:
            continue
        for at in range(len(pdf)):
            text = pdf[at].get_textpage().get_text_range()
            if len(text) > best[0] and text.split():
                best = (len(text), path.read_bytes(), at + 1, text.split()[-1])
    return best[1], best[2], best[3]


def citing(figure: str):
    """Claude cites the block holding the figure, wherever it is, and says it."""

    def reply(_path: str, body: dict) -> dict:
        system = system_of(body)
        if "search query that stands alone" in system:
            return plan_reply(body, query=figure)
        if "Keep what a later follow-up" in system:
            return message(body, [{"type": "text", "text": "A short summary."}])
        results = [
            b
            for b in body["messages"][-1]["content"]
            if isinstance(b, dict) and b.get("type") == "search_result"
        ]
        for at, result in enumerate(results):
            for block, content in enumerate(result["content"]):
                if figure in content["text"]:
                    cited = {
                        "type": "search_result_location",
                        "source": result["source"],
                        "title": result["title"],
                        "cited_text": content["text"],
                        "search_result_index": at,
                        "start_block_index": block,
                        "end_block_index": block + 1,
                    }
                    text = f"It gives {figure}."
                    return message(
                        body, [{"type": "text", "text": text, "citations": [cited]}]
                    )
        raise AssertionError("no passage held the figure")

    return reply


def test_the_route_answers_within_50_ms_on_the_fullest_page(
    library, tmp_path, app_client, monkeypatch
):
    """The whole route, the saved answer, the element and the verified
    file included, on the page holding the most text in the synthetic library."""
    path, figure = fullest_page()
    root = tmp_path / "fullest"
    root.mkdir()
    shutil.copy(path, root / path.name)
    sources.add_source(str(root), states.STANDARD)
    drain()
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = citing(figure)
        found = ask(app_client, new_thread(app_client), f"What is {figure} for?")
    saved = found["messages"][-1]
    (citation,) = last_answer(found)["citations"]
    route = (
        f"/api/documents/{citation['document_id']}/elements/"
        f"{citation['element_id']}/figures"
    )
    asked = {"message": saved["id"], "sentence": 0}
    assert app_client.get(route, params=asked).json()["figures"], "placed"
    started = time.perf_counter()
    app_client.get(route, params=asked)
    assert time.perf_counter() - started < 0.05


def fullest_page() -> tuple[Path, str]:
    """The upright PDF page in the synthetic library holding the most text, and an
    amount on it found there once."""
    best: tuple[int, Path, str] = (0, DATA, "")
    for path in DATA.rglob("*.pdf"):
        try:
            pdf = pypdfium2.PdfDocument(path)
        except pypdfium2.PdfiumError:
            continue
        for at in range(len(pdf)):
            page = pdf[at]
            if page.get_rotation():
                continue
            text = page.get_textpage().get_text_range()
            amounts = [
                f.text
                for f in agent_figures.figures_in(text)
                if f.kind == "money" and text.count(f.text) == 1
            ]
            if len(text) > best[0] and amounts:
                best = (len(text), path, amounts[0])
    return best[1], best[2]


def test_placing_takes_under_50_ms_on_the_largest_page():
    data, page, word = largest_page()
    locator = {"page": page, "rects": [WHOLE_PAGE]}
    figure_places.pdf_places(data, locator, [Shown(word, word)])  # pdfium warmed
    started = time.perf_counter()
    figure_places.pdf_places(data, locator, [Shown(word, word)])
    assert time.perf_counter() - started < 0.05
