"""`npm run verify:models`: every fixture through the real models on this Mac, which
the checks can't do. Never part of `npm run check`."""

import io
import json
import shutil
from pathlib import Path

import pypdfium2
import pytest
import sqlalchemy as sa
from transformers import AutoTokenizer

from backend import (
    build_mode,
    database,
    embedder,
    macos,
    revisions,
    search,
    sources,
    states,
)
from testing.queue import drain
from worker import ocr, page_reader, parse
from worker.content import MAX_TOKENS, split_to_limit

SLACK = 0.02
pytestmark = pytest.mark.real_models
DATA = Path(__file__).parents[2] / "data"
KEYS = json.loads((DATA / "evals/calibration/locators.json").read_text())


@pytest.fixture
def real(library, monkeypatch, tmp_path):
    monkeypatch.delenv(build_mode.STUB_MODELS_ENV, raising=False)
    parse.use_recorded(None)
    ocr.use_stand_in(None)
    page_reader.use_stand_in(None)
    return tmp_path


def test_every_format_with_real_models(real):
    folder = real / "library"
    folder.mkdir()
    for relative, expected in KEYS.items():
        target = folder / Path(relative).name
        shutil.copy(DATA / "library" / relative, target)
        if expected.get("stand_in") == "dataless":
            macos.use_dataless_stand_in(target)
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        rows = connection.execute(sa.select(database.documents)).mappings().all()
    by_name = {row["file_name"]: row for row in rows}
    for relative, expected in KEYS.items():
        macos.clear_dataless_stand_in(folder / Path(relative).name)
        row = by_name[Path(relative).name]
        if "status" in expected:
            assert row["status"] == expected["status"], relative
            continue
        assert row["status"] == states.INDEXED, (relative, row["status_reason"])
        elements = revisions.active_elements(row["id"])
        if "text" in expected:
            elements = [
                e for e in elements if expected["text"].lower() in e["text"].lower()
            ]
        assert elements, relative


@pytest.mark.parametrize("name", ["Rotated page.pdf", "Cropped page.pdf"])
def test_a_rectangle_lands_on_its_words_after_rotation_and_crop(real, name):
    """Apple Vision finds the word on the page as displayed, and the element's
    normalized rectangle must hold it."""
    source = DATA / "library" / "Home" / name
    folder = real / "one"
    folder.mkdir()
    shutil.copy(source, folder / name)
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        document = connection.execute(sa.select(database.documents)).mappings().one()
    elements = revisions.active_elements(document["id"])
    element = next(e for e in elements if "quartz" in e["text"])
    rect = element["locator"]["rects"][0]
    picture = pypdfium2.PdfDocument(source.read_bytes())[0].render(scale=2).to_pil()
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    word = next(w for w in ocr.read_words(buffer.getvalue()) if "quartz" in w.text)
    x, y = (word.box[0] + word.box[2]) / 2, (word.box[1] + word.box[3]) / 2
    assert rect["x0"] - SLACK <= x <= rect["x1"] + SLACK, (rect, word.box)
    assert rect["y0"] - SLACK <= y <= rect["y1"] + SLACK, (rect, word.box)


@pytest.mark.parametrize(
    "text",
    ["1234567890" * 3000, "!?#&*" * 3000, "日本語の文書" * 1500, "é ü ñ word " * 3000],
)
def test_the_token_ceiling_holds_by_the_real_tokenizer(real, text):
    """Dense numbers, punctuation and Unicode, counted independently of the splitter's
    own use of the count."""
    tokenizer = AutoTokenizer.from_pretrained(embedder.MODEL)
    for piece in split_to_limit(text):
        assert len(tokenizer.encode(piece, add_special_tokens=False)) <= MAX_TOKENS


def test_meaning_finds_a_paraphrase_on_real_embeddings(real, tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    (folder / "Lease.txt").write_text(
        "The tenant may keep one cat or one dog with the landlord's written consent."
    )
    (folder / "Roof.txt").write_text(
        "Two flashing seams near the chimney should be resealed within a year."
    )
    sources.add_source(str(folder), states.STANDARD)
    drain()
    embedder.start_loading()
    found = search.run("am I allowed to have animals in the flat", search.Scope())
    assert found["semantic"]
    assert found["passages"][0]["document"]["title"] == "Lease"


def test_the_local_model_writes_full_cards(real, tmp_path):
    """Every indexable fixture through the real local model. At least 95% get a card."""
    folder = tmp_path / "w"
    shutil.copytree(DATA / "library" / "Home", folder)
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        rows = (
            connection.execute(
                sa.select(database.cards.c.state)
                .join(
                    database.documents,
                    database.documents.c.id == database.cards.c.document_id,
                )
                .where(database.documents.c.status == states.INDEXED)
            )
            .scalars()
            .all()
        )
    complete = sum(1 for state in rows if state == states.COMPLETE)
    print(f"cards: {complete} of {len(rows)}")
    assert complete / len(rows) >= 0.95
