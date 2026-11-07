"""The end-to-end tests' stub Ollama answers each kind of request the app sends it."""

import json

from testing.e2e_stubs import ollama_reply


def _content(body: dict) -> str:
    return ollama_reply("/api/chat", body)["message"]["content"]


def test_a_picture_read_with_a_prompt_gets_a_figure():
    body = {"model": "gemma4:26b", "format": "json", "messages": []}
    assert json.loads(_content(body)) == {"kind": "figure", "description": "A figure."}


def test_a_helper_with_a_schema_gets_its_shape():
    plan = {"model": "gemma4:26b", "format": {"title": "plan"}, "messages": []}
    assert json.loads(_content(plan))["kind"] == "lookup"
    card = {"model": "gemma4:26b", "format": {"title": "card"}, "messages": []}
    assert json.loads(_content(card))["summary"] == "A card from the stub."


def test_an_answer_with_no_format_is_text():
    assert _content({"model": "gemma4:26b", "messages": []}).startswith("The local")
