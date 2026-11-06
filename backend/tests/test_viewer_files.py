"""What the viewer is given: sandboxed bytes, or a state its banner explains."""

import pytest
import sqlalchemy as sa

from backend import database, sources, states
from testing.queue import drain


@pytest.fixture
def indexed(library, tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    (folder / "page.html").write_text(
        '<html><body><p>Marker saffron.</p><img src="http://example.test/x.png">'
        "<script>alert(1)</script></body></html>"
    )
    (folder / "notes.txt").write_text("Marker pepper.")
    sources.add_source(str(folder), states.STANDARD)
    drain()
    return folder


def doc_id(name: str) -> str:
    with database.read() as connection:
        return connection.execute(
            sa.select(database.documents.c.id).where(
                database.documents.c.file_name == name
            )
        ).scalar_one()


def test_a_file_is_sandboxed(indexed, app_client):
    response = app_client.get(f"/api/documents/{doc_id('notes.txt')}/file")
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_html_goes_only_as_an_attachment(indexed, app_client):
    response = app_client.get(f"/api/documents/{doc_id('page.html')}/file")
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["content-type"] == "application/octet-stream"


def test_the_html_view_is_stored_text_with_no_markup(indexed, app_client):
    text = app_client.get(f"/api/documents/{doc_id('page.html')}/text").json()
    joined = " ".join(e["text"] for e in text["elements"])
    assert "saffron" in joined
    assert "<img" not in joined
    assert "<script" not in joined


class TestStates:
    def test_ready(self, indexed, app_client):
        detail = app_client.get(f"/api/documents/{doc_id('notes.txt')}").json()
        assert detail["view_state"] == "ready"

    def test_changed_refuses_the_bytes(self, indexed, app_client):
        (indexed / "notes.txt").write_text("Edited after it was indexed.")
        document = doc_id("notes.txt")
        assert (
            app_client.get(f"/api/documents/{document}").json()["view_state"]
            == "changed"
        )
        assert app_client.get(f"/api/documents/{document}/file").status_code == 409

    def test_deleted_is_missing_and_keeps_stored_text(self, indexed, app_client):
        (indexed / "notes.txt").unlink()
        document = doc_id("notes.txt")
        assert (
            app_client.get(f"/api/documents/{document}").json()["view_state"]
            == "missing"
        )
        text = app_client.get(f"/api/documents/{document}/text").json()
        assert "pepper" in text["elements"][0]["text"]


def test_a_card_edit_survives_and_is_marked(indexed, app_client):
    document = doc_id("notes.txt")
    app_client.patch(f"/api/documents/{document}/card", json={"summary": "My words."})
    detail = app_client.get(f"/api/documents/{document}").json()
    assert detail["card"]["summary"] == "My words."
    assert detail["card"]["edited_fields"] == ["summary"]
    refused = app_client.patch(f"/api/documents/{document}/card", json={"nope": 1})
    assert refused.status_code == 422
