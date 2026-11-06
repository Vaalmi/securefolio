"""`npm run seed`: builds the development vault from data/library, running the worker
until the queue is empty. Uses real models unless SECUREFOLIO_STUB_MODELS=1."""

import json
from pathlib import Path

from backend import jobs, macos, settings, sources, states, vault
from backend.migrations import run as migrations
from worker import main as worker_main

LIBRARY = Path(__file__).parents[1] / "library"
KEYS = LIBRARY.parent / "evals" / "calibration" / "locators.json"
LIMIT = 10_000


def main() -> None:
    seed_from(LIBRARY)


def seed_from(library: Path) -> None:
    vault.use_model_cache()
    migrations.upgrade()
    sources.ensure_library_source()
    settings.put(settings.SETUP_DONE, True)
    beside = library.parent / "locators.json"
    keys = json.loads((beside if beside.exists() else KEYS).read_text(encoding="utf-8"))
    for relative, expected in keys.items():
        if expected.get("stand_in") == "dataless":
            macos.use_dataless_stand_in((library / relative).resolve())
    folders = json.loads((library / "folders.json").read_text(encoding="utf-8"))
    watched = {source["path"] for source in sources.live()}
    for name, level in folders.items():
        path = str((library / name).resolve())
        if path not in watched:
            sources.add_source(path, level, mcp_enabled=level == states.STANDARD)
    for _ in range(LIMIT):
        job = jobs.claim()
        if job is None:
            break
        worker_main.run_job(job)
    print(f"Seeded {vault.root()}")


if __name__ == "__main__":
    main()
