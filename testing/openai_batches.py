"""OpenAI's side of the Files and Batches APIs for the tests, and the readers the batch
tests share."""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import sqlalchemy as sa

from backend import database, keychain, providers, settings, sources, spend, states
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OPENAI_ENV
from testing.queue import drain
from testing.stub_models import completion, stub_anthropic, stub_openai
from worker import batches

LUNA = "openai:gpt-6-luna"
LUNA_PRICE = (0.10, 0.50)  # per million tokens, as the catalog has it
CARD = {
    "title": "Lease",
    "summary": "A lease for unit 3B.",
    "doc_type": "Contract",
    "dates": [],
    "people": [],
    "organizations": [],
    "amounts": [],
    "tags": ["home"],
}
USAGE = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}


class OpenAIBatches:
    """OpenAI's side of the Files and Batches APIs, as far as the worker asks."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.batches: dict[str, dict] = {}
        self.last_upload = ""  # the JSONL of the upload in hand

    def reply(self, path: str, body: dict) -> dict | str:
        if path == "/files" and not body:
            return self._upload()
        if path.endswith("/content"):
            return self.files[path.split("/")[2]]
        if path == "/batches" and body:
            return self._create(body)
        if path.startswith("/batches?") or path == "/batches":
            listed = sorted(self.batches, key=lambda b: -self.batches[b]["created_at"])
            data = [self._shape(b) for b in listed]
            return {"object": "list", "data": data, "has_more": False}
        return self._shape(path.rsplit("/", maxsplit=1)[-1])

    def _upload(self) -> dict:
        file_id = f"file-in-{len(self.files) + 1}"
        self.files[file_id] = self.last_upload
        return {
            "id": file_id,
            "object": "file",
            "bytes": len(self.last_upload),
            "created_at": int(time.time()),
            "filename": "cards.jsonl",
            "purpose": "batch",
            "status": "processed",
        }

    def _create(self, body: dict) -> dict:
        batch_id = f"batch_{len(self.batches) + 1}"
        text = self.files[body["input_file_id"]]
        lines = [json.loads(line) for line in text.splitlines() if line.strip()]
        self.batches[batch_id] = {
            "status": "in_progress",
            "created_at": int(time.time()),
            "metadata": body.get("metadata") or {},
            "lines": lines,
            "output_file_id": None,
            "error_file_id": None,
        }
        return self._shape(batch_id)

    def finish(
        self, batch_id: str, status: str = "completed", answered: set | None = None
    ) -> None:
        """Ends the batch: `answered` custom ids get a card, the rest an error line."""
        batch = self.batches[batch_id]
        batch["status"] = status
        if status == "failed":
            return
        ids = [line["custom_id"] for line in batch["lines"]]
        answered = set(ids) if answered is None else answered
        outputs = [
            self._output(line)
            for line in batch["lines"]
            if line["custom_id"] in answered
        ]
        errors = [
            {
                "custom_id": i,
                "response": None,
                "error": {"code": status, "message": "-"},
            }
            for i in ids
            if i not in answered
        ]
        for kind, lines in (("output", outputs), ("error", errors)):
            if lines:
                file_id = f"file-{kind}-{batch_id}"
                self.files[file_id] = "\n".join(json.dumps(line) for line in lines)
                batch[f"{kind}_file_id"] = file_id

    @staticmethod
    def _output(line: dict) -> dict:
        body = completion(line["body"], json.dumps(CARD))
        body["usage"] = USAGE
        return {
            "custom_id": line["custom_id"],
            "response": {"status_code": 200, "request_id": "r", "body": body},
            "error": None,
        }

    def _shape(self, batch_id: str) -> dict:
        batch = self.batches[batch_id]
        return {
            "id": batch_id,
            "object": "batch",
            "endpoint": "/v1/chat/completions",
            "input_file_id": "file-in",
            "completion_window": "24h",
            "status": batch["status"],
            "output_file_id": batch["output_file_id"],
            "error_file_id": batch["error_file_id"],
            "created_at": batch["created_at"],
            "metadata": batch["metadata"],
        }


@contextmanager
def openai_batches(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """OpenAI's stub, with its key, as the helper's provider: `.side` is its batches."""
    keychain.set_key("test-openai-key", providers.OPENAI)
    settings.put(settings.MODEL_HELPER, LUNA)
    side = OpenAIBatches()
    with stub_openai() as stub:

        def reply(path: str, body: dict) -> dict | str:
            raw = stub.requests[-1].raw
            if path == "/files" and not body:
                start = raw.index('{"custom_id"')
                side.last_upload = raw[start : raw.rindex("}") + 1]
            return side.reply(path, body)

        stub.reply = reply
        monkeypatch.setenv(STUB_OPENAI_ENV, stub.url)
        stub.side = side
        yield stub
    keychain.delete_key(providers.OPENAI)


@contextmanager
def anthropic_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        keychain.set_key("test-anthropic-key", providers.ANTHROPIC)
        yield stub
    keychain.delete_key(providers.ANTHROPIC)


def backlog(tmp_path, count: int = spend.BATCH_ABOVE + 1) -> str:
    root = tmp_path / "backlog"
    root.mkdir()
    for n in range(count):
        (root / f"Doc {n}.txt").write_text(f"Document {n}.\n")
    source = sources.add_source(str(root), states.STANDARD, auto_organize=True)
    drain()
    return source["id"]


def the_batch() -> dict:
    with database.read() as connection:
        row = connection.execute(sa.select(database.message_batches)).mappings().one()
    return dict(row)


def items() -> dict[str, str]:
    with database.read() as connection:
        rows = connection.execute(sa.select(database.batch_items)).mappings().all()
    return {row["document_id"]: row["state"] for row in rows}


def billed() -> list[dict]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(database.egress_log).where(
                database.egress_log.c.detail.like("batch %")
            )
        ).mappings()
        return [dict(row) for row in rows]


def written_by() -> set[str]:
    with database.read() as connection:
        return set(connection.execute(sa.select(database.cards.c.written_by)).scalars())


def submitted(stub, tmp_path) -> dict:
    backlog(tmp_path)
    batches.tick()
    return the_batch()
