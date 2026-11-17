"""The providers' sides for the evaluation's tests: an answer on OpenAI citing its
first source, and a judge on Anthropic that finds every citation supported."""

import json

from testing.stub_models import completion, openai_reply

SOURCE_TAG = '<source id=\\"'  # how a marked answer's request looks, JSON-escaped


def judged(_path: str, body: dict) -> dict:
    """Anthropic's side: the judge finds every citation supported."""
    return {
        "id": "msg",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": [{"type": "text", "text": json.dumps({"supported": True})}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def answering_with(text: str):
    """OpenAI's side: the plan as usual, and an answer that says `text`."""

    def reply(path: str, body: dict) -> dict:
        if SOURCE_TAG not in json.dumps(body):
            return openai_reply(path, body)
        return completion(body, text)

    return reply


answered = answering_with("The pet deposit is $299.11. [1]")
