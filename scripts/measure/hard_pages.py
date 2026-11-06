"""Measurements on the calibration fixtures, with the real models: which bad text
layers are caught, how tables read, and how the local model reads charts.
Run: uv run python scripts/measure/hard_pages.py {routing,tables,charts,memory}"""

import io
import json
import re
import sys
import threading
import time
from pathlib import Path

import psutil
import pypdfium2

from agent import models
from backend import settings, vault
from evals.tables import exact_cell_match, markdown_to_grid, teds, value_within
from worker import hard_pages, ocr, page_reader, parse

ROOT = (
    Path(__file__).parent.parent.parent
    / "data"
    / "evals"
    / "calibration"
    / "hard_pages"
)
OLLAMA = "http://127.0.0.1:11434/api/chat"
LOCAL_MODEL = "gemma4:26b"


def fixtures(part: str) -> list[dict]:
    return json.loads((ROOT / f"{part}.json").read_text())


def page_signals(path: Path) -> hard_pages.PageSignals:
    document = pypdfium2.PdfDocument(path)
    page = document[0]
    width, height = page.get_size()
    textpage = page.get_textpage()
    cells = []
    for index in range(textpage.count_rects()):
        left, bottom, right, top = textpage.get_rect(index)
        text = textpage.get_text_bounded(left, bottom, right, top)
        box = (left / width, 1 - top / height, right / width, 1 - bottom / height)
        cells.append(ocr.Word(text, 1.0, box))
    buffer = io.BytesIO()
    page.render(scale=2).to_pil().save(buffer, "PNG")
    has_text = any(c.text.strip() for c in cells)
    return hard_pages.PageSignals(
        has_native_text=has_text,
        text_cells=cells,
        vision_words=ocr.read_words(buffer.getvalue()),
    )


def routing(part: str) -> None:
    bad = good = caught = false_alarms = 0
    measured = []
    for fixture in fixtures(part):
        if fixture["kind"] not in {"bad_text_layer", "good_text_layer"}:
            continue
        signals = page_signals(ROOT / part / fixture["file"])
        reasons = hard_pages.text_layer_reasons(signals)
        cells = hard_pages._outside_pictures(signals.text_cells, signals.picture_boxes)
        vision = hard_pages._outside_pictures(
            signals.vision_words, signals.picture_boxes
        )
        cer = hard_pages.character_error_rate(
            hard_pages._reading_order(cells), hard_pages._reading_order(vision)
        )
        cov = hard_pages.coverage(cells, vision)
        measured.append(
            (fixture["kind"], round(cer, 3), round(cov, 3), fixture["file"])
        )
        if fixture["kind"] == "bad_text_layer":
            bad += 1
            caught += bool(reasons)
        else:
            good += 1
            false_alarms += bool(reasons)
    print(
        json.dumps(
            {
                "part": part,
                "bad": bad,
                "caught": caught,
                "good": good,
                "read_again": false_alarms,
            }
        )
    )
    for row in sorted(measured):
        print(row)


def tables(part: str) -> None:
    scores = []
    for fixture in fixtures(part):
        if fixture["kind"] not in {"table", "table_scan"}:
            continue
        pdf = (ROOT / part / fixture["file"]).read_bytes()
        started = time.time()
        document = page_reader.read_page(pdf, 1)
        grid = markdown_to_grid(document.export_to_markdown())
        expected = fixture["expected"]["cells"]
        if fixture["expected"].get("pages"):
            continue_doc = page_reader.read_page(pdf, 2)
            more = markdown_to_grid(continue_doc.export_to_markdown())
            grid = grid + more[1:] if more[:1] == grid[:1] else grid + more
        scores.append(
            {
                "file": fixture["file"],
                "teds": round(teds(grid, expected), 3),
                "exact": round(exact_cell_match(grid, expected), 3),
                "seconds": round(time.time() - started, 1),
            }
        )
        print(scores[-1], flush=True)
    print(
        json.dumps(
            {
                "part": part,
                "tables": len(scores),
                "mean_teds": sum(s["teds"] for s in scores) / len(scores),
                "mean_exact": sum(s["exact"] for s in scores) / len(scores),
            }
        )
    )


def chart_values(pdf: Path) -> dict[str, float]:
    """Through what the product does: Docling finds the picture, it's cropped from the
    page, the local model reads it, and the table goes into the element's text."""
    data = pdf.read_bytes()
    result = parse._convert(pdf.name, data)  # noqa: SLF001
    elements = parse.pdf_elements(result.document)
    values: dict[str, float] = {}
    # The figures job's own model, as the worker reads it at the start of the job.
    tag = models.local_tag(settings.LOCAL_FIGURES)
    for element in hard_pages._waiting_pictures(elements):  # noqa: SLF001
        picture = hard_pages._render(data, element.locator.page)[1]  # noqa: SLF001
        crop = hard_pages._crop(picture, element)  # noqa: SLF001
        hard_pages._read_one_picture(element, crop, tag)  # noqa: SLF001
        for line in element.text.splitlines():
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) > 1 and not set(cells[-1]) <= {"-", " "}:
                values[cells[0]] = cells[-1]
    return values


def charts(part: str) -> None:
    vault.use_model_cache()
    within = total = 0
    for fixture in fixtures(part):
        if fixture["kind"] != "chart":
            continue
        expected = fixture["expected"]
        read = chart_values(ROOT / part / fixture["file"])
        scale = expected["full_scale"]
        if expected["chart"] == "pie":  # read as percents
            truth = {k: 100 * v / scale for k, v in expected["values"].items()}
            scale = 100
        else:
            truth = expected["values"]
        hits = sum(
            k in read
            and value_within(float(re.sub(r"[^\d.]", "", str(read[k])) or 0), v, scale)
            for k, v in truth.items()
        )
        within += hits
        total += len(truth)
        print(fixture["file"], hits, "of", len(truth), flush=True)
    print(
        json.dumps(
            {
                "part": part,
                "values": total,
                "within_2_percent": within,
                "share": within / total,
            }
        )
    )


def memory() -> None:
    process = psutil.Process()
    peak = [0]
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(0.2):
            peak[0] = max(peak[0], process.memory_info().rss)

    threading.Thread(target=watch, daemon=True).start()
    pdf = (ROOT / "tuning" / "table_clean_scan.pdf").read_bytes()
    page_reader.read_page(pdf, 1)
    stop.set()
    print(json.dumps({"page_reader_peak_gb": round(peak[0] / 2**30, 2)}))


if __name__ == "__main__":
    action = sys.argv[1]
    part = sys.argv[2] if sys.argv[2:] else "tuning"
    if sys.argv[3:]:
        ROOT = Path(sys.argv[3])
    {"routing": routing, "tables": tables, "charts": charts}.get(
        action, lambda _p: memory()
    )(part)
