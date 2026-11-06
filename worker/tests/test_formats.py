"""One fixture per format indexes end to end, with its expected locator."""

import io
import json
import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa
from PIL import Image

from backend import database, documents, jobs, macos, revisions, sources, states
from backend.database import document_locations
from data.build.record_docling import IMAGE_SUFFIXES, RECORDED, key_of
from testing.queue import drain
from worker import parse

DATA = Path(__file__).parents[2] / "data"
KEYS = json.loads((DATA / "evals/calibration/locators.json").read_text())


@pytest.fixture
def scanned_library(library, tmp_path):
    """The format fixtures in one watched folder, scanned and indexed."""
    folder = tmp_path / "library"
    folder.mkdir()
    for relative in KEYS:
        target = folder / Path(relative).name
        shutil.copy(DATA / "library" / relative, target)
        if KEYS[relative].get("stand_in") == "dataless":
            macos.use_dataless_stand_in(target)
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    yield source, folder
    for relative in KEYS:
        macos.clear_dataless_stand_in(folder / Path(relative).name)


def document_named(name: str) -> dict:
    with database.read() as connection:
        row = (
            connection.execute(
                sa.select(database.documents).where(
                    database.documents.c.file_name == name
                )
            )
            .mappings()
            .first()
        )
    assert row is not None, name
    return dict(row)


def test_every_format_ends_where_it_should_with_its_locator(scanned_library):
    for relative, expected in KEYS.items():
        document = document_named(Path(relative).name)
        if "status" in expected:
            assert document["status"] == expected["status"], relative
            assert document["status_reason"], relative
            continue
        assert document["status"] == states.INDEXED, (
            relative,
            document["status_reason"],
        )
        elements = revisions.active_elements(document["id"])
        assert elements, relative
        if "text" in expected:
            elements = [
                e for e in elements if expected["text"].lower() in e["text"].lower()
            ]
            assert elements, relative
        locator = elements[0]["locator"]
        for key in set(expected) & {
            "kind",
            "page",
            "sheet",
            "range",
            "slide",
            "message",
            "start",
            "end",
            "anchor",
        }:
            assert locator[key] == expected[key], (relative, key, locator)


@pytest.mark.parametrize(
    "relative", [r for r in KEYS if Path(r).suffix.lower() in IMAGE_SUFFIXES]
)
def test_a_pictures_recording_is_found_however_its_png_is_compressed(relative):
    """Pillow saves the same pixels as different PNG bytes on macOS and Linux."""
    data = (DATA / "library" / relative).read_bytes()
    png = parse._to_png(data, parse.KIND_OF_SUFFIX[Path(relative).suffix.lower()])
    saved = {png}
    with Image.open(io.BytesIO(png)) as picture:
        for level in (0, 9):
            buffer = io.BytesIO()
            picture.save(buffer, "PNG", compress_level=level)
            saved.add(buffer.getvalue())
    assert len(saved) > 1
    keys = {key_of(each, None) for each in saved}
    assert len(keys) == 1
    assert (RECORDED / f"{keys.pop()}.json").exists()


def test_a_table_keeps_its_rows_and_a_card_is_written_pending(scanned_library):
    document = document_named("Household budget.xlsx")
    with database.read() as connection:
        card = (
            connection.execute(
                sa.select(database.cards).where(
                    database.cards.c.document_id == document["id"]
                )
            )
            .mappings()
            .one()
        )
        chunk = connection.execute(
            sa.select(database.chunks.c.text).where(
                database.chunks.c.document_id == document["id"]
            )
        ).scalar_one()
    assert card["state"] == states.PENDING
    assert "sheet Bills" in chunk
    assert "Columns: Item, Jan, Feb" in chunk


def test_attachments_are_listed_and_quoted_mail_is_a_message(scanned_library):
    document = document_named("Lease email.eml")
    elements = revisions.active_elements(document["id"])
    assert any("terms.pdf" in e["text"] and e["kind"] == "attachment" for e in elements)
    assert all(">" not in e["text"] for e in elements if "tarragon" in e["text"])


def test_a_dataless_file_is_listed_not_read_and_found_by_name(
    scanned_library, monkeypatch
):
    document = document_named("In the cloud.pdf")
    assert document["status"] == states.NOT_DOWNLOADED
    assert document["content_sha256"] is None
    with database.read() as connection:
        hits = (
            connection.exec_driver_sql(
                "SELECT document_id FROM documents_fts "
                "WHERE documents_fts MATCH 'cloud'"
            )
            .scalars()
            .all()
        )
    assert document["id"] in hits
    rows = documents.locations(document["id"])
    assert rows[0]["dataless"]


def test_a_dataless_file_on_disk_is_never_opened(library, tmp_path, monkeypatch):
    from backend import originals  # noqa: PLC0415

    folder = tmp_path / "f"
    folder.mkdir()
    placeholder = folder / "p.pdf"
    placeholder.write_bytes(b"")
    macos.use_dataless_stand_in(placeholder)
    opened = []
    monkeypatch.setattr(originals, "hash_file", opened.append)
    monkeypatch.setattr(originals, "read_bytes", opened.append)
    sources.add_source(str(folder), states.STANDARD)
    drain()
    macos.clear_dataless_stand_in(placeholder)
    assert opened == []


def test_a_scan_marks_a_folder_needing_attention_when_it_holds_placeholders(
    scanned_library,
):
    source, _folder = scanned_library
    assert sources.get(source["id"])["status"] == states.NEEDS_ATTENTION


def test_each_location_is_read_only_and_no_bytes_change(scanned_library):
    _source, folder = scanned_library
    with database.read() as connection:
        rows = connection.execute(sa.select(document_locations)).mappings().all()
    assert rows
    for relative in KEYS:
        name = Path(relative).name
        assert (folder / name).read_bytes() == (
            DATA / "library" / relative
        ).read_bytes()


def test_the_jobs_ran_in_priority_order():
    assert jobs.PRIORITY_DROP < jobs.PRIORITY_CHANGE < jobs.PRIORITY_BULK
    assert jobs.PRIORITY_BULK < jobs.PRIORITY_HARD_PAGES < jobs.PRIORITY_ORGANIZE
