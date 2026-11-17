"""For local evaluation runs against the stub Ollama."""

import json
import time
from typing import Any

from evals import run
from testing.stub_models import ollama_reply

TAG = "stub-local:7b"
LOCAL = f"{run.LOCAL_PREFIX}{TAG}"


def planning_after(seconds: float):
    """Ollama, listing TAG: planning answers after `seconds`; the answer cites."""

    def reply(path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path == "/api/tags":
            return {"models": [{"name": "gemma4:26b"}, {"name": TAG}]}
        found = ollama_reply(path, body)
        if path != "/api/chat":
            return found
        sent = json.dumps(body).replace('\\"', '"')
        if "stands alone" in sent:
            time.sleep(seconds)
            return found
        if "<source id=" in sent:
            text = "The pet deposit is $299.11 [1]."
            return {**found, "message": {"role": "assistant", "content": text}}
        return found

    return reply
