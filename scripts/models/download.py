"""`npm run models`: downloads the search, OCR and page-reading models into the
development vault, as first run does in the app, and waits until each is verified.
`npm run seed` needs them, unless it runs with SECUREFOLIO_STUB_MODELS=1."""

import sys
import time

from backend import model_store, vault
from backend.migrations import run as migrations

POLL_SECONDS = 2


def _progress() -> str:
    rows = model_store.status()
    done = sum(row["bytes_done"] for row in rows)
    total = sum(row["bytes_total"] for row in rows) or 1
    return f"{done / 1e9:.2f} of {total / 1e9:.2f} GB"


def _wait() -> None:
    while model_store.downloading():
        print(_progress(), flush=True)
        time.sleep(POLL_SECONDS)


def main() -> int:
    vault.use_model_cache()
    migrations.upgrade()
    # What an earlier run left part way first, as the app does at startup.
    model_store.resume_at_startup()
    _wait()
    model_store.start_download()
    _wait()
    failed = [r for r in model_store.status() if r["state"] != model_store.INSTALLED]
    for row in failed:
        reason = row["reason"] or f"stopped while {row['state']}"
        print(f"{row['model']}: {reason}", file=sys.stderr)
    if not failed:
        print(f"All models are in {vault.model_cache()}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
