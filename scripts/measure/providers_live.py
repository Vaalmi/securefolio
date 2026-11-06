"""The live runs, one provider at a time, on a copy of the development vault with
real models. Each spends real money, within $1: every paid call is written to the
egress log as an attempt at its estimate before it goes, and here one whose estimate
doesn't fit in what's left isn't made, whatever asks for it (a question, Deep, a
card, a batch). A Deep run whose estimate doesn't fit isn't consented to. A step
stops at its first failure, before anything after it is asked.
The report lists every call, with its provider and cost.

Keys come from .env and stay in this process's memory: the Keychain is never read or
written (the key test stores a key that works, so it goes to the stand-in store). A
key's ID comes from .env too, where it needs one: ANTHROPIC_WORKSPACE_ID for an unscoped
Anthropic key, OPENAI_PROJECT_ID for an OpenAI key tied to a project.

Run, with the vault a copy and .env the main checkout's:
  PYTHONPATH=. HF_HUB_OFFLINE=1 SECUREFOLIO_MODEL_CACHE=<main>/.cache/models \\
  SECUREFOLIO_VAULT=<copy> uv run --env-file <main>/.env python \\
  scripts/measure/providers_live.py anthropic|anthropic_card|openai|batch|local
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend import (
    auth,
    connectivity,
    database,
    egress,
    jobs,
    keychain,
    model_catalog,
    providers,
    settings,
    spend,
    threads,
    vault,
)
from backend.main import NO_WORKER_ENV, create_app
from worker import batches, organize
from worker import main as worker_main

PORT = 18799
HOST = f"http://127.0.0.1:{PORT}"
DONE = (threads.COMPLETED, threads.FAILED, threads.STOPPED)
BUDGET_USD = 1.0
OK = 200
BATCH_POLL_SECONDS = 60
BATCH_LIMIT_SECONDS = 90 * 60


class Stopped(Exception):
    """A step failed, or would go past the limit: nothing after it is asked."""


def _keys_in_memory() -> None:
    """The Keychain stays out of it: the stand-in store, which the key test writes."""
    keychain._use_stand_in = lambda: True  # noqa: SLF001


def _within_the_limit(started: float) -> None:
    """Each paid call is recorded as an attempt at its estimate first: a question's by
    `egress.record`, background work's and a batch's by `egress.reserve`. One that
    wouldn't fit in what's left isn't recorded, so it isn't made. One call at a time:
    the steps wait for each run and drain the queue in this thread."""
    record, reserve = egress.record, egress.reserve

    def fits(entry: egress.Entry) -> bool:
        return entry.outcome != egress.ATTEMPTED or entry.cost_usd <= _left(started)

    def recorded(entry: egress.Entry) -> int:
        if not fits(entry):
            raise Stopped(f"a {entry.purpose} call at ${entry.cost_usd} doesn't fit")
        return record(entry)

    def reserved(entry: egress.Entry, since: float, cap: float) -> int | None:
        return reserve(entry, since, cap) if fits(entry) else None

    egress.record = recorded
    egress.reserve = reserved


def _client() -> TestClient:
    app = create_app(PORT, auth.new_tokens(), write_runtime=False)
    headers = {"Authorization": f"Bearer {app.state.tokens.app}"}
    return TestClient(app, base_url=HOST, headers=headers)


def _since(started: float) -> list[dict[str, Any]]:
    log = database.egress_log
    with database.read() as connection:
        rows = connection.execute(
            sa.select(log).where(log.c.at >= started).order_by(log.c.at)
        ).mappings()
        return [
            {
                "provider": row["provider"],
                "model": row["model"],
                "purpose": row["purpose"],
                "outcome": row["outcome"],
                "tokens": row["input_tokens"] + row["output_tokens"],
                "cost_usd": round(row["cost_usd"], 5),
            }
            for row in rows
        ]


def _spent(started: float) -> float:
    return round(sum(row["cost_usd"] for row in _since(started)), 5)


def _left(started: float) -> float:
    return BUDGET_USD - _spent(started)


def _guard(started: float) -> None:
    """Before each paid step: once nothing's left, the step stops there."""
    if _left(started) <= 0:
        raise Stopped(f"at the live run's limit: ${_spent(started)}")


def _wait(run_id: str) -> None:
    while threads.get_run(run_id)["state"] not in (*DONE, threads.PAUSED):
        time.sleep(0.05)


def _ask(
    client: TestClient, started: float, thread: str, text: str, mode: str
) -> dict[str, Any]:
    _guard(started)
    run = client.post(
        f"/api/threads/{thread}/messages", json={"text": text, "mode": mode}
    ).json()
    if "id" not in run:
        raise Stopped(f"refused: {run}")
    _wait(run["id"])
    state = threads.get_run(run["id"])
    if state["state"] == threads.PAUSED:  # the Deep run's cost, asked first
        card = state["card"]
        if card["estimate_usd"] > _left(started):
            raise Stopped(f"Deep estimated at ${card['estimate_usd']}, past the limit")
        run = client.post(
            f"/api/threads/{thread}/resume",
            json={"card_id": card["id"], "choice": "deep"},
        ).json()
        _wait(run["id"])
        state = threads.get_run(run["id"])
    if state["state"] != threads.COMPLETED:
        raise Stopped(f"the run ended {state['state']}: {state['error']}")
    answer = threads.transcript(thread)[-1]["answer"]
    return {
        "model": answer["model"],
        "model_name": answer.get("model_name"),
        "local": answer["local"],
        "cost_usd": round(answer["cost_usd"], 5),
        "citations": len(answer.get("citations", [])),
        "uncited": sum(1 for p in answer.get("parts", []) if p.get("uncited")),
        "text": "".join(p["text"] for p in answer.get("parts", []))[:200],
    }


def _thread(client: TestClient, scope: dict[str, Any] | None = None) -> str:
    return client.post("/api/threads", json={"scope": scope}).json()["id"]


def _drain() -> None:
    while (job := jobs.claim()) is not None:
        worker_main.run_job(job)


def _folder(names: list[str]) -> Path:
    """A new folder of a few short documents of its own: text the vault holds nowhere
    else, since a document with the same text as another is that document."""
    root = Path(tempfile.mkdtemp(prefix="securefolio-live-"))
    stamp = int(time.time())
    for name in names:
        (root / f"Service invoice {name}.txt").write_text(
            f"Invoice {name}-{stamp} from Harbor Plumbing Co., 14 Pier Road.\n"
            f"Date: October {3 + len(name)}, 2026. Repaired the kitchen sink trap and "
            f"replaced a shut-off valve. Total due: ${120 + len(name) * 7}.50, by "
            "November 30, 2026.\n"
        )
    return root


def _cards_of(source_id: str) -> list[dict[str, Any]]:
    with database.read() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.select(database.cards.c.state, database.cards.c.written_by)
                .join(
                    database.document_locations,
                    database.document_locations.c.document_id
                    == database.cards.c.document_id,
                )
                .where(database.document_locations.c.source_id == source_id)
            ).mappings()
        ]


def _key_test(client: TestClient, provider: str, scope: str | None) -> dict[str, Any]:
    key = os.environ[keychain.DEVELOPMENT_KEY_ENVS[provider]]
    body = {"key": key, **({"scope_id": scope} if scope else {})}
    tested = client.post(f"/api/settings/keys/{provider}/test", json=body)
    row = tested.json()
    if tested.status_code != OK:
        raise Stopped(f"the {provider} key test failed: {tested.status_code} {row}")
    return {
        "status": tested.status_code,
        "hint_shown": row.get("hint"),
        "scope_id": row.get("scope_id"),
        "needs_id": row.get("needs_id"),
    }


def _openai_key_test(client: TestClient) -> dict[str, Any]:
    """With the Project ID from .env, if there is one, as a key tied to a project
    needs it."""
    return _key_test(client, providers.OPENAI, os.environ.get("OPENAI_PROJECT_ID"))


def _organized_folder(helper: str, folder: Path) -> dict[str, Any]:
    """A new folder with cloud organizing on, at the helper and price it costs now, as
    the app sends them from the estimate it shows."""
    price = model_catalog.price(helper)
    return {
        "path": str(folder),
        "sensitivity": "standard",
        "auto_organize": True,
        "model": helper,
        "input_usd": price[0] if price else None,
        "output_usd": price[1] if price else None,
    }


def _cloud_cards(
    client: TestClient, started: float, helper: str, names: list[str]
) -> dict[str, Any]:
    _guard(started)
    settings.put(settings.MODEL_HELPER, helper)
    folder = _folder(names)
    added = client.post("/api/sources", json=_organized_folder(helper, folder))
    if added.status_code != OK:
        raise Stopped(f"the folder was refused: {added.json()}")
    _drain()
    return {"cards": _cards_of(added.json()["id"])}


def _choose_anthropic() -> None:
    """Anthropic's models for its question and its Deep one, whatever the vault had
    saved: its Deep check is Anthropic's, and charges only Anthropic."""
    settings.put(settings.MODEL_ANSWER, "anthropic:claude-sonnet-5-5")
    settings.put(settings.MODEL_DEEP, "anthropic:claude-opus-5-5")


def anthropic(started: float, report: dict[str, Any]) -> None:
    with _client() as client:
        report["key_test"] = _key_test(
            client, providers.ANTHROPIC, os.environ.get("ANTHROPIC_WORKSPACE_ID")
        )
        _choose_anthropic()
        thread = _thread(client)
        report["question"] = _ask(
            client, started, thread, "When does the car warranty end?", "standard"
        )
        report["follow_up"] = _ask(client, started, thread, "Who sold it?", "standard")
        report["deep"] = _ask(
            client,
            started,
            _thread(client),
            "Compare what the roof report and the energy audit recommend.",
            "deep",
        )
        report["card"] = _cloud_cards(
            client, started, "anthropic:claude-haiku-5-5", ["A1"]
        )


def anthropic_card(started: float, report: dict[str, Any]) -> None:
    with _client() as client:
        _key_test(client, providers.ANTHROPIC, os.environ.get("ANTHROPIC_WORKSPACE_ID"))
        report["card"] = _cloud_cards(
            client, started, "anthropic:claude-haiku-5-5", ["A2"]
        )


def openai(started: float, report: dict[str, Any]) -> None:
    with _client() as client:
        report["key_test"] = _openai_key_test(client)
        settings.put(settings.MODEL_ANSWER, "openai:gpt-6.1-sol")
        report["question"] = _ask(
            client,
            started,
            _thread(client),
            "When does the car warranty end?",
            "standard",
        )
        report["card"] = _cloud_cards(client, started, "openai:gpt-6-luna", ["O1"])
        settings.put(settings.MODEL_ANSWER, "anthropic:claude-sonnet-5-5")


def batch(started: float, report: dict[str, Any]) -> None:
    """One small OpenAI batch: three cards, batched though fewer than a backlog."""
    # Both: batches.py copies organize's figure when it's imported.
    organize.BACKLOG_ALONE = batches.ALONE = 0
    with _client() as client:
        if not keychain.get_key(providers.OPENAI):
            report["key_test"] = _openai_key_test(client)
        settings.put(settings.MODEL_HELPER, "openai:gpt-6-luna")
        folder = _folder(["B1", "B2", "B3"])
        added = client.post(
            "/api/sources", json=_organized_folder("openai:gpt-6-luna", folder)
        ).json()
        if "id" not in added:
            raise Stopped(f"the folder was refused: {added}")
        _drain()
        report["estimate"] = client.get(
            f"/api/sources/{added['id']}/organize-estimate"
        ).json()
        begun = time.monotonic()
        states_now: list[str] = []
        while time.monotonic() - begun < BATCH_LIMIT_SECONDS:
            batches.tick()
            _drain()
            with database.read() as connection:
                states_now = list(
                    connection.execute(
                        sa.select(database.message_batches.c.state).where(
                            database.message_batches.c.provider == providers.OPENAI
                        )
                    ).scalars()
                )
            if states_now and all(s == batches.APPLIED for s in states_now):
                break
            _guard(started)
            time.sleep(BATCH_POLL_SECONDS)
        report["batches"] = states_now
        report["seconds"] = round(time.monotonic() - begun)
        report["cards"] = _cards_of(added["id"])


def local(started: float, report: dict[str, Any]) -> None:
    with _client() as client:
        scope = {"kind": "library", "include_private": True, "label": "All"}
        report["private"] = _ask(
            client,
            started,
            _thread(client, scope),
            "What did the clinic visit find?",
            "standard",
        )
        settings.put(settings.OFFLINE, True)
        connectivity.forget()
        report["offline"] = _ask(
            client,
            started,
            _thread(client),
            "When does the car warranty end?",
            "standard",
        )
        settings.put(settings.OFFLINE, False)
        # Cards waiting for a cards model that's gone, then one chosen.
        settings.put(settings.MODEL_HELPER, settings.LOCAL)
        installed = str(settings.get(settings.LOCAL_CARDS))
        settings.put(settings.LOCAL_CARDS, "no-such-model:latest")
        added = client.post(
            "/api/sources",
            json={"path": str(_folder(["L1", "L2"])), "sensitivity": "standard"},
        ).json()
        _drain()
        report["cards_waiting"] = _cards_of(added["id"])
        chose = client.patch("/api/settings", json={"local_cards": installed})
        report["chose_cards_model"] = {"status": chose.status_code, "tag": installed}
        _drain()
        report["cards_after"] = _cards_of(added["id"])


STEPS = {
    "anthropic": anthropic,
    "anthropic_card": anthropic_card,
    "openai": openai,
    "batch": batch,
    "local": local,
}


def main() -> None:
    step = sys.argv[1]
    vault.use_model_cache()
    os.environ[NO_WORKER_ENV] = "1"
    _keys_in_memory()
    started = time.time()
    _within_the_limit(started)
    report: dict[str, Any] = {}
    try:
        STEPS[step](started, report)
    except Stopped as stopped:
        report["stopped"] = str(stopped)
    report["egress"] = _since(started)
    report["spent_usd"] = _spent(started)
    report["cap_usd"] = float(settings.get(settings.SPEND_CAP))
    report["spent_this_month_usd"] = round(spend.spent_this_month(), 4)
    out = Path(".cache") / f"providers_live_{step}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
