"""A local model's scores, measured on this Mac and kept in the library,
`<vault>/models/measured.json` (written by `npm run eval -- --record-from <run>
--record-local-to <vault>`). They're tied to the build that was measured, Ollama's
digest for the tag: a tag rebuilt since shows none of them."""

import json
from pathlib import Path
from typing import Any

from backend import model_catalog, vault

FILE = Path("models") / "measured.json"


def path_in(root: Path) -> Path:
    return root / FILE


def _kept() -> dict[str, Any]:
    found = path_in(vault.root())
    if not found.exists():
        return {}
    try:
        return dict(json.loads(found.read_text()).get("models") or {})
    except (ValueError, AttributeError):
        return {}  # a file someone broke shows as not measured, not as an error


def of(tag: str, build: str | None) -> dict[str, Any] | None:
    """For an installed tag: its scores, when the build Ollama lists now is the one
    measured; the two builds, when it was rebuilt since; None when never measured."""
    kept = _kept().get(tag)
    if not kept:
        return None
    if kept.get("digest") != build:
        return {
            "scores": None,
            "build": None,
            "changed": {"measured": kept.get("digest"), "installed": build},
        }
    scores = {k: v for k, v in kept.items() if k not in ("digest", "kept")}
    return {"scores": model_catalog.shown(scores), "build": build, "changed": None}
