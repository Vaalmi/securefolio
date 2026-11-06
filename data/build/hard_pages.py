"""Builds the hard-page fixtures, each with its expected cells, values or text and
the reasons its page is hard. Everything comes from the seed's word lists."""

import io
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pypdfium2
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle

rl_config.invariant = 1
WIDTH, HEIGHT = letter
MARGIN = 72
_WORD_LIST = (
    "account balance coverage deposit invoice notice payment premium quarter reserve "
    "renewal schedule statement tenant warranty audit budget contract estimate filing "
    "garage harbor inspection ledger maintenance policy receipt repair roof survey"
)
WORDS = _WORD_LIST.split()
GOOD_TEXT_PAGES = 20
BAD_TEXT_PAGES = 10
TABLE_VARIANT = 2
FIGURE_VARIANT = 3


@dataclass
class Fixture:
    file: str
    page: int
    kind: str
    reasons: list[str]
    expected: dict[str, Any] = field(default_factory=dict)


def sentence(rng: random.Random, words: int = 12) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(words)).capitalize() + "."


def lines_of_text(rng: random.Random, count: int = 28) -> list[str]:
    return [sentence(rng) for _ in range(count)]


def amount(rng: random.Random) -> str:
    return f"{rng.randint(10, 9999)}.{rng.randint(0, 99):02d}"


def table_rows(rng: random.Random, rows: int, columns: int) -> list[list[str]]:
    header = [rng.choice(WORDS).capitalize() + f" {i}" for i in range(columns)]
    body = [
        [rng.choice(WORDS).capitalize(), *[amount(rng) for _ in range(columns - 1)]]
        for _ in range(rows)
    ]
    return [header, *body]


def draw_lines(canvas: Canvas, lines: list[str], top: float = HEIGHT - MARGIN) -> None:
    y = top
    canvas.setFont("Helvetica", 10)
    for line in lines:
        canvas.drawString(MARGIN, y, line[:95])
        y -= 16


def draw_table(
    canvas: Canvas, rows: list[list[str]], top: float, styles: list[Any]
) -> None:
    table = Table(rows)
    table.setStyle(TableStyle(styles))
    _, height = table.wrapOn(canvas, WIDTH - 2 * MARGIN, HEIGHT)
    table.drawOn(canvas, MARGIN, top - height)


GRID = [("GRID", (0, 0), (-1, -1), 0.5, colors.black)]


def rasterize(pdf: bytes) -> list[bytes]:
    document = pypdfium2.PdfDocument(pdf)
    images = []
    for page in document:
        buffer = io.BytesIO()
        page.render(scale=2).to_pil().save(buffer, "PNG")
        images.append(buffer.getvalue())
    return images


def scanned_pdf(images: list[bytes], layers: list[list[str]] | None = None) -> bytes:
    """A PDF of page images, with an optional hidden text layer on each."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    for index, image in enumerate(images):
        canvas.drawImage(ImageReader(io.BytesIO(image)), 0, 0, WIDTH, HEIGHT)
        if layers:
            text = canvas.beginText(MARGIN, HEIGHT - MARGIN)
            text.setTextRenderMode(3)
            text.setFont("Helvetica", 10)
            text.setLeading(16)
            for line in layers[index]:
                text.textLine(line[:95])
            canvas.drawText(text)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def text_pdf(lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    draw_lines(canvas, lines)
    canvas.save()
    return buffer.getvalue()


def chart_image(rng: random.Random, kind: str) -> tuple[bytes, dict[str, Any]]:
    labels = [
        rng.choice(WORDS).capitalize() + f" {i}" for i in range(rng.randint(3, 5))
    ]
    values = [rng.randint(10, 95) for _ in labels]
    figure, axes = plt.subplots(figsize=(6, 4))
    if kind == "bar":
        axes.bar(labels, values)
    elif kind == "line":
        axes.plot(labels, values, marker="o")
    else:
        axes.pie(values, labels=labels, autopct="%d%%")
    if kind != "pie":
        axes.set_ylim(0, 100)
    axes.set_title(f"{rng.choice(WORDS).capitalize()} by {rng.choice(WORDS)}")
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=150)
    plt.close(figure)
    full_scale = 100 if kind != "pie" else sum(values)
    return buffer.getvalue(), {
        "values": dict(zip(labels, values, strict=True)),
        "full_scale": full_scale,
    }


class Builder:
    def __init__(self, out: Path, seed: int) -> None:
        self.out = out
        self.rng = random.Random(seed)  # noqa: S311
        self.fixtures: list[Fixture] = []
        out.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, pdf: bytes) -> str:
        (self.out / name).write_bytes(pdf)
        return name

    def add_tables(self) -> None:
        rng = self.rng
        for scanned in (False, True):
            suffix = "scan" if scanned else "digital"
            # borderless: no ruling lines at all
            rows = table_rows(rng, 8, 4)
            self._table(f"table_borderless_{suffix}", rows, [], ["weak_table"], scanned)
            # merged headers
            rows = table_rows(rng, 6, 4)
            rows.insert(0, ["Group", "", "Totals", ""])
            spans = [*GRID, ("SPAN", (0, 0), (1, 0)), ("SPAN", (2, 0), (3, 0))]
            self._table(f"table_merged_{suffix}", rows, spans, ["weak_table"], scanned)
            # clean, with normal blanks
            rows = table_rows(rng, 8, 4)
            rows[3][2] = ""
            self._table(f"table_clean_{suffix}", rows, GRID, [], scanned)
        self._table_across_pages()

    def _table(
        self,
        name: str,
        rows: list[list[str]],
        styles: list[Any],
        reasons: list[str],
        scanned: bool,
    ) -> None:
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=letter)
        draw_lines(canvas, [sentence(self.rng)], HEIGHT - MARGIN)
        draw_table(canvas, rows, HEIGHT - MARGIN - 40, styles)
        canvas.save()
        pdf = buffer.getvalue()
        if scanned:
            pdf = scanned_pdf(rasterize(pdf))
        kind = "table_scan" if scanned else "table"
        self.fixtures.append(
            Fixture(self.save(f"{name}.pdf", pdf), 1, kind, reasons, {"cells": rows})
        )

    def _table_across_pages(self) -> None:
        rows = table_rows(self.rng, 30, 3)
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=letter)
        draw_table(canvas, rows[:18], HEIGHT - MARGIN, GRID)
        canvas.showPage()
        draw_table(canvas, rows[18:], HEIGHT - MARGIN, GRID)
        canvas.save()
        name = self.save("table_across.pdf", buffer.getvalue())
        self.fixtures.append(
            Fixture(name, 1, "table", ["weak_table"], {"cells": rows, "pages": 2})
        )

    def add_charts(self) -> None:
        for kind in ("bar", "line", "pie"):
            for index in range(3):
                image, expected = chart_image(self.rng, kind)
                buffer = io.BytesIO()
                canvas = Canvas(buffer, pagesize=letter)
                draw_lines(canvas, [sentence(self.rng)])
                canvas.drawImage(ImageReader(io.BytesIO(image)), MARGIN, 300, 468, 312)
                canvas.save()
                name = self.save(f"chart_{kind}_{index}.pdf", buffer.getvalue())
                self.fixtures.append(
                    Fixture(name, 1, "chart", [], {"chart": kind, **expected})
                )

    def add_figure_and_layout(self) -> None:
        rng = self.rng
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=letter)
        canvas.setFillColor(colors.lightblue)
        canvas.rect(MARGIN, 200, 468, 450, fill=1)
        canvas.setFillColor(colors.black)
        caption = sentence(rng, 5)
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(MARGIN + 40, 420, caption)
        canvas.save()
        name = self.save(
            "figure_with_text.pdf", scanned_pdf(rasterize(buffer.getvalue()))
        )
        self.fixtures.append(
            Fixture(name, 1, "figure", ["pictures_over_half"], {"text": caption})
        )

        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=letter)
        canvas.setFont("Helvetica", 9)
        for column, x in enumerate((MARGIN, 330)):
            y = HEIGHT - MARGIN
            for _ in range(24):
                canvas.drawString(x, y, sentence(rng, 5)[:40])
                y -= 14
            if column == 1:
                canvas.rect(x - 8, y - 4, 220, HEIGHT - MARGIN - y + 16)
        canvas.save()
        self.fixtures.append(
            Fixture(
                self.save("two_column_sidebar.pdf", buffer.getvalue()), 1, "layout", []
            )
        )

    def add_text_layers(
        self, bad: int = BAD_TEXT_PAGES, good: int = GOOD_TEXT_PAGES
    ) -> None:
        rng = self.rng
        for index in range(bad):
            lines = lines_of_text(rng)
            image = rasterize(text_pdf(lines))
            mode = ("garbled", "wrong", "partial")[index % 3]
            if mode == "garbled":
                layer = ["".join(rng.choice("x#@~qz%") for _ in line) for line in lines]
            elif mode == "wrong":
                layer = lines_of_text(rng)
            else:
                layer = lines[: len(lines) * 2 // 5]
            name = self.save(f"bad_{mode}_{index}.pdf", scanned_pdf(image, [layer]))
            self.fixtures.append(
                Fixture(
                    name,
                    1,
                    "bad_text_layer",
                    ["bad_text_layer"],
                    {"text": " ".join(lines)},
                )
            )
        for index in range(good):
            lines = lines_of_text(rng)
            variant = index % 4
            if variant == TABLE_VARIANT:  # a clean table on the page
                buffer = io.BytesIO()
                canvas = Canvas(buffer, pagesize=letter)
                draw_lines(canvas, lines[:10])
                draw_table(canvas, table_rows(rng, 6, 3), HEIGHT - 300, GRID)
                canvas.save()
                pdf = buffer.getvalue()
            elif variant == FIGURE_VARIANT:  # a small figure on the page
                image, _ = chart_image(rng, "bar")
                buffer = io.BytesIO()
                canvas = Canvas(buffer, pagesize=letter)
                draw_lines(canvas, lines[:10])
                canvas.drawImage(ImageReader(io.BytesIO(image)), MARGIN, 100, 300, 200)
                canvas.save()
                pdf = buffer.getvalue()
            else:
                pdf = text_pdf(lines)
            name = self.save(f"good_{index}.pdf", pdf)
            self.fixtures.append(
                Fixture(name, 1, "good_text_layer", [], {"text": " ".join(lines)})
            )

    def add_grounding(self) -> None:
        rng = self.rng
        phrase = sentence(rng, 6)
        name = self.save(
            "repeated_phrases.pdf", scanned_pdf(rasterize(text_pdf([phrase] * 20)))
        )
        self.fixtures.append(
            Fixture(
                name, 1, "grounding", [], {"phrase": phrase, "precision": "element"}
            )
        )
        rows = table_rows(rng, 8, 3)
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=letter)
        draw_table(canvas, rows, HEIGHT - MARGIN, GRID)
        canvas.save()
        name = self.save(
            "reordered_cells.pdf", scanned_pdf(rasterize(buffer.getvalue()))
        )
        self.fixtures.append(
            Fixture(name, 1, "grounding", [], {"cells": rows, "precision": "element"})
        )
        self._transformed()

    def _transformed(self) -> None:
        rng = self.rng
        lines = lines_of_text(rng, 20)
        unique = sentence(rng, 9)
        lines[5] = unique
        for name, rotate, crop in (
            ("rotated_90", 90, None),
            ("cropped", 0, (36, 100, 576, 760)),
        ):
            images = rasterize(text_pdf(lines))
            if (
                rotate
            ):  # stored sideways and rotated for display, as landscape pages are
                sideways = io.BytesIO()
                Image.open(io.BytesIO(images[0])).rotate(rotate, expand=True).save(
                    sideways, "PNG"
                )
                images = [sideways.getvalue()]
            reader = PdfReader(io.BytesIO(scanned_pdf(images)))
            writer = PdfWriter()
            page = reader.pages[0]
            if rotate:
                page.rotate(rotate)
            if crop:
                page.cropbox = RectangleObject(crop)
            writer.add_page(page)
            buffer = io.BytesIO()
            writer.write(buffer)
            saved = self.save(f"{name}.pdf", buffer.getvalue())
            self.fixtures.append(
                Fixture(
                    saved,
                    1,
                    "transformed",
                    [],
                    {
                        "phrase": unique,
                        "precision": "words",
                        "rotate": rotate,
                        "crop": crop,
                    },
                )
            )

    def write_key(self, key_path: Path) -> None:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(
            json.dumps([vars(f) for f in self.fixtures], indent=1), encoding="utf-8"
        )


def build(
    out: Path,
    seed: int,
    key_path: Path,
    bad: int = BAD_TEXT_PAGES,
    good: int = GOOD_TEXT_PAGES,
) -> list[Fixture]:
    builder = Builder(out, seed)
    builder.add_tables()
    builder.add_charts()
    builder.add_figure_and_layout()
    builder.add_text_layers(bad, good)
    builder.add_grounding()
    builder.write_key(key_path)
    return builder.fixtures


def main() -> None:
    root = Path(__file__).parent.parent / "evals" / "calibration" / "hard_pages"
    build(root / "tuning", 101, root / "tuning.json")
    build(root / "checking", 202, root / "checking.json")


if __name__ == "__main__":
    main()
