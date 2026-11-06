"""Stub Anthropic, OpenAI-compatible (OpenAI and OpenRouter) and Ollama servers on
loopback, which record every request. The real SDKs talk to them, so the privacy
tests read the actual bytes sent."""

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

# A reply is the JSON to send, or for a file's content, its text.
Reply = Callable[[str, dict[str, Any]], dict[str, Any] | str]


@dataclass
class Recorded:
    path: str
    body: dict[str, Any]
    raw: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class StubServer:
    url: str
    requests: list[Recorded] = field(default_factory=list)
    reply: Reply | None = None
    status: int = 200

    def all_text(self) -> str:
        """Everything received, for a privacy test to search."""
        return "\n".join(request.raw for request in self.requests)


def _anthropic_reply(_path: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "msg_stub",
        "type": "message",
        "role": "assistant",
        "model": body.get("model", "stub"),
        "content": [{"type": "text", "text": "stub answer"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _ollama_reply(path: str, body: dict[str, Any]) -> dict[str, Any]:
    if path == "/api/tags":
        return {"models": [{"name": "gemma4:26b"}]}
    if path == "/api/show":
        return {"capabilities": ["completion", "vision"]}
    if path == "/api/ps":
        return {"models": []}
    return {
        "model": body.get("model", "stub"),
        "message": {"role": "assistant", "content": "stub local answer"},
        "done": True,
    }


OPENROUTER_LIST = [
    {
        "id": "openai/gpt-oss-120b",
        "name": "OpenAI: gpt-oss-120b",
        "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
        "architecture": {
            "input_modalities": ["text"],
            "output_modalities": ["text"],
        },
        "supported_parameters": ["structured_outputs", "response_format"],
    },
    {
        "id": "anthropic/claude-sonnet-5-5",
        "name": "Anthropic: Claude Sonnet 5.5",
        "pricing": {"prompt": "0.000002", "completion": "0.00001"},
        "architecture": {
            "input_modalities": ["text", "image"],
            "output_modalities": ["text"],
        },
        "supported_parameters": ["structured_outputs"],
    },
    {
        "id": "openrouter/auto",
        "name": "Auto Router",
        "pricing": {"prompt": "-1", "completion": "-1"},
        "architecture": {
            "input_modalities": ["text", "image"],
            "output_modalities": ["text"],
        },
        "supported_parameters": [],
    },
]


def _openai_reply(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Chat Completions, as OpenAI and OpenRouter answer; OpenRouter's key check and
    model list too."""
    if path.endswith("/models"):
        return {"data": OPENROUTER_LIST}
    if path.endswith("/key"):
        return {"data": {"label": "stub", "usage": 0}}
    return {
        "id": "chatcmpl_stub",
        "object": "chat.completion",
        "created": 0,
        "model": body.get("model", "stub"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "stub answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _openai_chunks(answer: dict[str, Any]) -> Iterator[str]:
    """The Chat Completions stream: the text, then the usage, then the end."""
    text = answer["choices"][0]["message"]["content"]
    base = {
        "id": answer["id"],
        "object": "chat.completion.chunk",
        "created": 0,
        "model": answer["model"],
    }
    delta = {
        "index": 0,
        "delta": {"role": "assistant", "content": text},
        "finish_reason": None,
    }
    yield f"data: {json.dumps({**base, 'choices': [delta]})}\n\n"
    done = {"index": 0, "delta": {}, "finish_reason": "stop"}
    yield f"data: {json.dumps({**base, 'choices': [done]})}\n\n"
    yield f"data: {json.dumps({**base, 'choices': [], 'usage': answer['usage']})}\n\n"
    yield "data: [DONE]\n\n"


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps({'type': event, **data})}\n\n"


def _stream_events(answer: dict[str, Any]) -> Iterator[str]:
    usage = answer.get("usage", {"input_tokens": 10, "output_tokens": 5})
    message = {**answer, "content": [], "usage": {**usage, "output_tokens": 0}}
    yield _sse("message_start", {"message": message})
    for at, block in enumerate(answer["content"]):
        yield _sse(
            "content_block_start",
            {"index": at, "content_block": {"type": "text", "text": ""}},
        )
        delta = {"type": "text_delta", "text": block["text"]}
        yield _sse("content_block_delta", {"index": at, "delta": delta})
        for citation in block.get("citations") or []:
            delta = {"type": "citations_delta", "citation": citation}
            yield _sse("content_block_delta", {"index": at, "delta": delta})
        yield _sse("content_block_stop", {"index": at})
    stopped = {"stop_reason": "end_turn", "stop_sequence": None}
    yield _sse("message_delta", {"delta": stopped, "usage": usage})
    yield _sse("message_stop", {})


def _body_of(content_type: str, raw: str) -> dict[str, Any]:
    """A request's JSON. A file upload is multipart, not JSON: it's kept only raw."""
    return json.loads(raw) if raw and "multipart/" not in content_type else {}


def _shape_of(answer: dict[str, Any] | str, body: dict[str, Any], path: str) -> str:
    """How a reply goes back: a file's text, Anthropic's events, OpenAI's chunks, or
    plain JSON."""
    if isinstance(answer, str):
        return "text"
    if body.get("stream") and "/messages" in path:
        return "events"
    if body.get("stream") and "/chat/completions" in path:
        return "chunks"
    return "json"


@contextmanager
def _serve(default: Reply, port: int = 0) -> Iterator[StubServer]:
    stub = StubServer(url="")

    class Handler(BaseHTTPRequestHandler):
        def _answer(self, raw: str) -> None:
            body = _body_of(self.headers.get("Content-Type", ""), raw)
            stub.requests.append(Recorded(self.path, body, raw, dict(self.headers)))
            answer = (stub.reply or default)(self.path, body)
            send = {
                "text": self._send_text,
                "events": self._stream_answer,
                "chunks": self._stream_chunks,
                "json": self._send_json,
            }[_shape_of(answer, body, self.path)]
            send(cast("Any", answer))

        def _send_json(self, answer: dict[str, Any]) -> None:
            payload = json.dumps(answer).encode()
            self.send_response(stub.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_text(self, text: str) -> None:
            """A file's content, as OpenAI's Files API sends it."""
            payload = text.encode()
            self.send_response(stub.status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _stream_answer(self, answer: dict[str, Any]) -> None:
            """The Anthropic SDK's event stream, citations included."""
            payload = "".join(_stream_events(answer)).encode()
            self.send_response(stub.status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _stream_chunks(self, answer: dict[str, Any]) -> None:
            """OpenAI's Chat Completions stream, with its usage last."""
            payload = "".join(_openai_chunks(answer)).encode()
            self.send_response(stub.status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self._answer("")

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self._answer(self.rfile.read(length).decode(errors="replace"))

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    stub.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()


def stub_anthropic(port: int = 0) -> Any:
    return _serve(_anthropic_reply, port)


def stub_ollama(port: int = 0) -> Any:
    return _serve(_ollama_reply, port)


def stub_openai(port: int = 0) -> Any:
    """For OpenAI, or for OpenRouter, which speaks the same API."""
    return _serve(_openai_reply, port)


# Replies and readers the provider tests share, as OpenAI and OpenRouter answer.


def system_of(body: dict) -> str:
    return " ".join(
        str(m.get("content", ""))
        for m in body.get("messages", [])
        if m.get("role") in ("system", "developer")
    )


def completion(body: dict, content: str) -> dict:
    return {
        "id": "chatcmpl",
        "object": "chat.completion",
        "created": 0,
        "model": body.get("model", "stub"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def openai_reply(_path: str, body: dict) -> dict:
    """As OpenAI and OpenRouter answer: the plan in JSON, a summary, or an answer."""
    system = system_of(body)
    if "stands alone" in system:
        plan = {"query": "rent", "kind": "lookup", "date_from": None, "date_to": None}
        return completion(body, json.dumps({**plan, "keywords": []}))
    if "Keep what a later follow-up" in system:
        return completion(body, "A short summary.")
    if '"actions"' in system or "memories" in system.casefold():
        return completion(body, json.dumps({"actions": []}))
    return completion(body, "The rent is due monthly. [1]")


def ollama_reply(path: str, body: dict) -> dict:
    """As this Mac's Ollama answers a chat: what `openai_reply` says, in Ollama's
    shape. Anything else, as `stub_ollama` does by default."""
    if path != "/api/chat":
        return _ollama_reply(path, body)
    content = openai_reply(path, body)["choices"][0]["message"]["content"]
    return {
        **_ollama_reply(path, body),
        "message": {"role": "assistant", "content": content},
    }


def answers_of(stub) -> list:
    """The answer requests: their evidence is in numbered `<source id=` tags."""
    return [r for r in stub.requests if '<source id=\\"' in r.raw]
