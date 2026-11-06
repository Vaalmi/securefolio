"""Writes backend/model_manifest.json from a complete model cache: each model's revision
and each file's size and hash, which the download verifies against."""

import hashlib
import json
import sys
from pathlib import Path

GROUPS = {
    "Qwen/Qwen3-Embedding-0.6B": "search",
    "Qwen/Qwen3-Reranker-0.6B": "search",
    "docling-project/docling-layout-heron": "search",
    "docling-project/docling-models": "search",
    "docling-project/DocumentFigureClassifier-v2.5": "search",
    "ibm-granite/granite-docling-258M-mlx": "page",
}
CHUNK = 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _model(hub: Path, repo: str) -> dict:
    home = hub / f"models--{repo.replace('/', '--')}"
    refs = {
        path.relative_to(home / "refs").as_posix(): path.read_text().strip()
        for path in (home / "refs").rglob("*")
        if path.is_file()
    }
    revision = next(iter(refs.values()))
    snapshot = home / "snapshots" / revision
    files = [
        {
            "path": path.relative_to(snapshot).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(snapshot.rglob("*"))
        if path.is_file()
    ]
    return {
        "repo": repo,
        "group": GROUPS[repo],
        "revision": revision,
        "refs": refs,
        "files": files,
    }


def main() -> None:
    hub = Path(sys.argv[1]) / "huggingface" / "hub"
    models = [_model(hub, repo) for repo in GROUPS]
    target = Path(__file__).parents[2] / "backend" / "model_manifest.json"
    target.write_text(json.dumps(models, indent=1) + "\n")


if __name__ == "__main__":
    main()
