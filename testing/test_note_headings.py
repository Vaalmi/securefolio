"""Note headings: a heading given when a note is saved, or its first 40 characters;
the heading and text changed together; an empty heading refused. That an edit leaves
sensitivity as it was is in test_privacy_notes.py."""

from testing.note_helpers import note_row


def test_a_heading_given_is_kept_and_none_is_the_first_40_characters(
    library, app_client
):
    given = app_client.post(
        "/api/notes", json={"body": "The roof needs work.", "heading": " Roof "}
    ).json()
    assert given["title"] == "Roof", "trimmed"
    text = "A note long enough that its heading is cut at forty characters."
    for heading in (None, "", "   "):
        made = app_client.post("/api/notes", json={"body": text, "heading": heading})
        assert made.json()["title"] == text[:40]


def test_the_heading_and_text_change_together(library, app_client):
    made = app_client.post("/api/notes", json={"body": "First."}).json()
    url = f"/api/notes/{made['id']}"
    edited = app_client.patch(url, json={"heading": " New ", "body": " Second. "})
    assert edited.status_code == 200
    row = note_row(made["id"])
    assert (row["title"], row["body"]) == ("New", "Second.")
    listed = app_client.get("/api/notes").json()
    assert (listed[0]["title"], listed[0]["body"]) == ("New", "Second.")


def test_an_empty_heading_or_text_is_refused_and_nothing_changes(library, app_client):
    made = app_client.post("/api/notes", json={"body": "Kept."}).json()
    url = f"/api/notes/{made['id']}"
    no_heading = app_client.patch(url, json={"heading": "  ", "body": "New."})
    assert no_heading.status_code == 422
    assert no_heading.json()["detail"] == "A note needs a heading."
    no_text = app_client.patch(url, json={"heading": "New", "body": " "})
    assert no_text.status_code == 422
    assert no_text.json()["detail"] == "A note needs some text."
    row = note_row(made["id"])
    assert (row["title"], row["body"]) == ("Kept.", "Kept.")
