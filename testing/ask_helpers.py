"""The ask flow the tests share: Anthropic's stub replies, and asking through the app
and waiting for its answer."""

import json
import time

NO_EVIDENCE = "I couldn't find enough evidence in this scope"


def cited_reply(body: dict, text: str = "The rent is due monthly.") -> dict:
    """Cites the first passage it was given, if it was given one."""
    results = [
        b
        for b in body["messages"][-1]["content"]
        if isinstance(b, dict) and b.get("type") == "search_result"
    ]
    if not results:
        content = [{"type": "text", "text": NO_EVIDENCE}]
    else:
        content = [
            {
                "type": "text",
                "text": text,
                "citations": [
                    {
                        "type": "search_result_location",
                        "source": results[0]["source"],
                        "title": results[0]["title"],
                        "cited_text": results[0]["content"][0]["text"],
                        "search_result_index": 0,
                        "start_block_index": 0,
                        "end_block_index": 1,
                    }
                ],
            }
        ]
    return message(body, content)


def message(body: dict, content: list[dict]) -> dict:
    return {
        "id": "msg",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": content,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def plan_reply(body: dict, query: str = "rent", kind: str = "lookup", **extra) -> dict:
    fields = {
        "query": query,
        "kind": kind,
        "date_from": None,
        "date_to": None,
        "keywords": [],
        **extra,
    }
    return message(body, [{"type": "text", "text": json_of(fields)}])


def json_of(fields: dict) -> str:
    return json.dumps(fields)


def system_of(body: dict) -> str:
    system = body.get("system", "")
    if isinstance(system, list):
        return " ".join(b.get("text", "") for b in system)
    return system


def default_reply(_path: str, body: dict) -> dict:
    if "search query that stands alone" in system_of(body):
        return plan_reply(body)
    if "Keep what a later follow-up" in system_of(body):
        return message(body, [{"type": "text", "text": "A short summary."}])
    return cited_reply(body)


def ask(client, thread_id: str, text: str, mode: str = "standard") -> dict:
    run = client.post(
        f"/api/threads/{thread_id}/messages", json={"text": text, "mode": mode}
    ).json()
    return finish(client, run)


def finish(client, run: dict) -> dict:
    deadline = time.time() + 20
    while time.time() < deadline:
        found = client.get(f"/api/threads/{run['thread_id']}").json()
        if found["active_run"] is None or found["active_run"]["state"] == "paused":
            return found
        time.sleep(0.02)
    raise AssertionError("the run didn't finish")


def new_thread(client, scope: dict | None = None) -> str:
    return client.post("/api/threads", json={"scope": scope}).json()["id"]


def last_answer(found: dict) -> dict:
    return found["messages"][-1]["answer"]
