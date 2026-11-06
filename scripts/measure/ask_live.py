"""The live run: a lookup, a follow-up, a list or total and a Deep answer on Claude,
then a Private and an Offline answer on the local model, on the development vault with
real models. Reports time to first step and first words, tokens and cost for each, and
checks the egress log against them. Spends real money, within the live run's limit.
Run: PYTHONPATH=. HF_HUB_OFFLINE=1 HF_HOME=$PWD/.cache/models/huggingface \\
  SECUREFOLIO_VAULT=$PWD/.vault-dev uv run --env-file .env python \\
  scripts/measure/ask_live.py"""

import json
import os
import time
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend import auth, connectivity, database, keychain, settings, threads, vault
from backend.main import NO_WORKER_ENV, create_app

PORT = 18799
HOST = f"http://127.0.0.1:{PORT}"
DONE = (threads.COMPLETED, threads.FAILED, threads.STOPPED)
BUDGET_USD = 2.0


def _wait(run_id: str) -> dict[str, float]:
    """Polls the events as they're written, timing what the app would see."""
    begun = time.perf_counter()
    first_step = first_word = 0.0
    last = 0
    while True:
        for row in threads.events_after(run_id, last):
            last = row["id"]
            now = time.perf_counter() - begun
            if row["event"] == "step" and not first_step:
                first_step = now
            if row["event"] == "token" and not first_word:
                first_word = now
        run = threads.get_run(run_id)
        if run["state"] in (*DONE, threads.PAUSED):
            return {
                "first_step_s": round(first_step, 3),
                "first_word_s": round(first_word, 3),
                "total_s": round(time.perf_counter() - begun, 2),
            }
        time.sleep(0.005)


def _ask(client: TestClient, thread: str, text: str, mode: str) -> dict[str, Any]:
    run = client.post(
        f"/api/threads/{thread}/messages", json={"text": text, "mode": mode}
    ).json()
    if "id" not in run:
        return {"refused": run}
    timing = _wait(run["id"])
    state = threads.get_run(run["id"])
    if state["state"] == threads.PAUSED:
        card = state["card"]
        run = client.post(
            f"/api/threads/{thread}/resume",
            json={"card_id": card["id"], "choice": "deep"},
        ).json()
        timing = {**_wait(run["id"]), "asked_first_usd": card["estimate_usd"]}
    return {**timing, **_reported(thread, threads.get_run(run["id"]))}


def _reported(thread: str, run: dict[str, Any]) -> dict[str, Any]:
    if run["state"] != threads.COMPLETED:
        return {"state": run["state"], "error": run["error"]}
    answer = threads.transcript(thread)[-1]["answer"]
    return {
        "kind": answer["kind"],
        "model": answer["model"],
        "local": answer["local"],
        "tokens": answer["tokens"],
        "cost_usd": round(answer["cost_usd"], 4),
        "citations": [
            f"{c['title']}: {c['label']}" for c in answer.get("citations", [])
        ],
        "uncited": sum(1 for p in answer.get("parts", []) if p.get("uncited")),
        "text": "".join(p["text"] for p in answer.get("parts", []))[:280],
        "table_total": (answer.get("table") or {}).get("total"),
        "pending": len((answer.get("table") or {}).get("pending", [])),
    }


def _new(client: TestClient, scope: dict[str, Any] | None = None) -> str:
    return client.post("/api/threads", json={"scope": scope}).json()["id"]


def _egress() -> dict[str, Any]:
    log = database.egress_log
    with database.read() as connection:
        rows = connection.execute(sa.select(log)).mappings().all()
    return {
        "calls": len(rows),
        "by_purpose": {
            purpose: round(
                sum(r["cost_usd"] for r in rows if r["purpose"] == purpose), 4
            )
            for purpose in {r["purpose"] for r in rows}
        },
        "total_usd": round(sum(r["cost_usd"] for r in rows), 4),
        "outcomes": sorted({r["outcome"] for r in rows}),
    }


def main() -> None:
    vault.use_model_cache()
    os.environ[NO_WORKER_ENV] = "1"
    keychain.get_key = lambda: os.environ["ANTHROPIC_API_KEY"]
    settings_app = create_app(PORT, auth.new_tokens(), write_runtime=False)
    headers = {"Authorization": f"Bearer {settings_app.state.tokens.app}"}
    report: dict[str, Any] = {}
    with TestClient(settings_app, base_url=HOST, headers=headers) as client:
        warm = _new(client)
        _ask(client, warm, "Warm the models up: what is a lease?", "standard")
        thread = _new(client)
        report["lookup"] = _ask(
            client, thread, "When does the car warranty end?", "standard"
        )
        report["follow_up"] = _ask(client, thread, "Who sold it?", "standard")
        report["follow_up_2"] = _ask(
            client, thread, "What vehicle is it for?", "standard"
        )
        report["follow_up_3"] = _ask(client, thread, "And which year?", "standard")
        report["list_total"] = _ask(
            client, _new(client), "What did I spend on insurance in 2025?", "standard"
        )
        report["deep"] = _ask(
            client,
            _new(client),
            "Compare what the roof report and the energy audit recommend.",
            "deep",
        )
        scope = {"kind": "library", "include_private": True, "label": "All"}
        report["private"] = _ask(
            client, _new(client, scope), "What did the clinic visit find?", "standard"
        )
        settings.put(settings.OFFLINE, True)
        connectivity.forget()
        report["offline"] = _ask(
            client, _new(client), "When does the car warranty end?", "standard"
        )
        settings.put(settings.OFFLINE, False)
    report["egress"] = _egress()
    print(json.dumps(report, indent=1, ensure_ascii=False))
    if report["egress"]["total_usd"] >= BUDGET_USD:
        raise SystemExit("Over the live run's limit.")


if __name__ == "__main__":
    main()
