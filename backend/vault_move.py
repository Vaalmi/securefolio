"""DocSynth's library, moved once to SecureFolio's folder. SecureFolio was called
DocSynth until 2026-11-06. On its first start, the app takes DocSynth's library folder
as its own, points the stored paths into it at the new place, and copies DocSynth's
cloud keys to SecureFolio's Keychain entries. Nothing moves while DocSynth's backend
holds its library, nor when SecureFolio already has one, nor when the environment
names the vault, as development and the tests do.

A marker written before the folder moves, and removed once the paths and keys are
done, makes the move resumable: a start that finds it finishes the work first. The
library's lock is held from before the move until the backend's own startup takes it
over, so no other backend opens a library half moved."""

import json
from pathlib import Path
from typing import IO

from backend import keychain, rebase, vault, vault_lock
from backend.migrations import run as migrations

MARKER = "moving-from-docsynth.json"
STILL_RUNNING = (
    "DocSynth is still running. Quit it, then open SecureFolio again: SecureFolio "
    "moves DocSynth's library over on its first start."
)
ALREADY_STARTING = "SecureFolio is already starting. Wait for it, then try again."


def _hold(lock: Path, refusal: str) -> IO[str]:
    held = vault_lock.try_hold(lock)
    if held is None:
        raise SystemExit(refusal)
    return held


def _finish(new: Path) -> None:
    """Paths and keys, then the marker: safe to run again after a crash part way."""
    marker = new / MARKER
    was = json.loads(marker.read_text(encoding="utf-8"))
    # A DocSynth stopped on its first start leaves a folder with no tables yet: the
    # schema is brought up to date first, as the backend's startup would.
    migrations.upgrade()
    rebase.library_paths(
        [
            (was["lexical"], str(new)),
            (was["resolved"], str(new.resolve())),
        ]
    )
    keychain.copy_legacy_keys()
    marker.unlink()


def _empty_or_missing(folder: Path) -> bool:
    """Through a link too: a link to nothing, or to an empty folder, counts as empty."""
    return not folder.exists() or not any(folder.iterdir())


def adopt_docsynth_library() -> IO[str] | None:
    """Moves DocSynth's library to SecureFolio's folder, or finishes a move a crash
    interrupted, before anything else in this process opens the vault. Returns the
    library's lock, still held, for vault_lock.acquire to keep; None when nothing
    moved."""
    old, new = vault.LEGACY_VAULT, vault.DEFAULT_VAULT
    if vault.configured():
        return None
    if (new / MARKER).is_file():
        held = _hold(new / vault.LOCK_NAME, ALREADY_STARTING)
        _finish(new)
        return held
    if not old.is_dir() or not _empty_or_missing(new):
        return None
    held = _hold(old / vault.LOCK_NAME, STILL_RUNNING)
    (old / MARKER).write_text(
        json.dumps({"lexical": str(old), "resolved": str(old.resolve())}),
        encoding="utf-8",
    )
    if new.is_symlink():
        new.unlink()  # a link to an empty folder: the library takes its place
    elif new.exists():
        new.rmdir()
    old.rename(new)
    _finish(new)
    return held
