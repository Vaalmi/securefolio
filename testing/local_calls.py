"""For tests of local model calls: the stub Ollama's chat replies, a helper's task, and
the calls a run of one recorded."""

import asyncio
import time
from typing import Any

from agent import api, models
from testing.stub_models import ollama_reply

TAG = "gemma4:26b"  # the stub Ollama lists it


def chat_replying(content: str, done_reason: str = "stop", stall: float = 0.0):
    """Ollama's reply to a chat: `content`, ended for `done_reason`, after `stall`
    seconds."""

    def reply(path: str, body: dict) -> dict:
        found = ollama_reply(path, body)
        if path != "/api/chat":
            return found
        time.sleep(stall)
        return {
            **found,
            "message": {"role": "assistant", "content": content},
            "done_reason": done_reason,
            "eval_count": 7,
            "prompt_eval_count": 11,
        }

    return reply


def task(purpose: api.Purpose, thread_id: str | None = "t") -> models.Task:
    return models.Task(
        purpose,
        "Plan a search query that stands alone.",
        [api.Part(api.PartKind.QUESTION, "When is the rent due?")],
        thread_id,
        True,
        local_tag=TAG,
    )


def recorded(call) -> tuple[Any, list[dict]]:
    """What `call` returns, and the calls it recorded."""
    with models.recording_calls():
        found = asyncio.run(call)
        return found, list(models._calls or [])  # noqa: SLF001
