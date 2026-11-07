"""The stub Anthropic, OpenAI and Ollama servers the end-to-end tests talk to, on fixed
ports. Their replies follow what each request asks, so a journey runs with no real
model. A key with "-any-" in it isn't tied to one workspace or project: sent without
its ID, the provider refuses it, as each one does."""

import json
import sys
import time
from threading import Thread
from typing import Any

from testing import stub_models

ANTHROPIC_PORT = 8101
OLLAMA_PORT = 8102
OPENAI_PORT = 8103
UNSCOPED = "-any-"  # in a key: it needs its Workspace or Project ID


def _system_of(body: dict[str, Any]) -> str:
    system = body.get("system", "")
    if isinstance(system, list):
        return " ".join(block.get("text", "") for block in system)
    return system


def _message(body: dict[str, Any], content: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "msg",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": content,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 400, "output_tokens": 60},
    }


def _plan(body: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(body["messages"])
    kind = "comparison" if "ompare" in raw else "lookup"
    keywords: list[str] = []
    if "spend" in raw or "spent" in raw:
        kind, keywords = "list", ["insurance"]
    fields = {
        "query": "report recommend",
        "kind": kind,
        "date_from": None,
        "date_to": None,
        "keywords": keywords,
    }
    return _message(body, [{"type": "text", "text": json.dumps(fields)}])


def _answer(body: dict[str, Any]) -> dict[str, Any]:
    results = [
        block
        for block in body["messages"][-1]["content"]
        if isinstance(block, dict) and block.get("type") == "search_result"
    ]
    content: list[dict[str, Any]] = []
    for at, result in enumerate(results[:3]):
        content.append(
            {
                "type": "text",
                "text": f"{result['title']} says what matters here. ",
                "citations": [
                    {
                        "type": "search_result_location",
                        "source": result["source"],
                        "title": result["title"],
                        "cited_text": result["content"][0]["text"],
                        "search_result_index": at,
                        "start_block_index": 0,
                        "end_block_index": 1,
                    }
                ],
            }
        )
    return _message(body, content or [{"type": "text", "text": "No evidence."}])


def anthropic_reply(_path: str, body: dict[str, Any]) -> dict[str, Any]:
    system = _system_of(body)
    if "stands alone" in system:
        return _plan(body)
    if "follow-up" in system:
        return _message(body, [{"type": "text", "text": "A short summary."}])
    return _answer(body)


def _local_json(schema: dict[str, Any]) -> dict[str, Any]:
    """The shape a helper asked for, from its schema's title."""
    title = schema.get("title")
    if title == "plan":
        return {
            "query": "report recommend",
            "kind": "lookup",
            "date_from": None,
            "date_to": None,
            "keywords": [],
        }
    if title == "memory_actions":
        return {"actions": []}
    return {
        "title": "",
        "summary": "A card from the stub.",
        "doc_type": "Other",
        "dates": [],
        "people": [],
        "organizations": [],
        "amounts": [],
        "tags": [],
    }


def ollama_reply(path: str, body: dict[str, Any]) -> dict[str, Any]:
    if path == "/api/tags":
        return {"models": [{"name": "gemma4:26b"}]}
    if path == "/api/show":  # what it can do, as Ollama says
        return {"capabilities": ["completion", "vision"]}
    if path == "/api/ps":
        return {"models": []}
    content = "The local model says so [1]."
    asked = body.get("format")
    if asked == "json":  # a picture, read with a prompt rather than a schema
        content = json.dumps({"kind": "figure", "description": "A figure."})
    elif asked:
        content = json.dumps(_local_json(asked))
    return {
        "model": body.get("model", "stub"),
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
    }


def _scoped(stub: Any, reply: Any, header: str, refusal: str) -> Any:
    """The reply, unless the key needs its ID and the request didn't name one."""

    def answer(path: str, body: dict[str, Any]) -> dict[str, Any]:
        sent = {k.casefold(): v for k, v in stub.requests[-1].headers.items()}
        key = sent.get("x-api-key") or sent.get("authorization", "")
        if UNSCOPED in key and not sent.get(header):
            stub.status = 400
            return {
                "type": "error",
                "error": {"type": "invalid_request_error", "message": refusal},
            }
        stub.status = 200
        return reply(path, body)

    return answer


def _serve(make: Any, reply_for: Any, port: int) -> None:
    with make(port) as stub:
        stub.reply = reply_for(stub)
        while True:
            time.sleep(3600)


def main() -> None:
    Thread(
        target=_serve,
        args=(stub_models.stub_ollama, lambda _stub: ollama_reply, OLLAMA_PORT),
        daemon=True,
    ).start()
    Thread(
        target=_serve,
        args=(
            stub_models.stub_openai,
            lambda stub: _scoped(
                stub,
                stub_models.openai_reply,
                "openai-project",
                "This key needs an OpenAI-Project header naming a project.",
            ),
            OPENAI_PORT,
        ),
        daemon=True,
    ).start()
    _serve(
        stub_models.stub_anthropic,
        lambda stub: _scoped(
            stub,
            anthropic_reply,
            "anthropic-workspace-id",
            "This API key is not scoped to a workspace.",
        ),
        ANTHROPIC_PORT,
    )


if __name__ == "__main__":
    sys.exit(main())
