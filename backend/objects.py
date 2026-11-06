"""The managed copies: files dragged in, kept content-addressed in objects/."""

import uuid
from pathlib import Path

from backend import macos, originals, vault


def _path_for(sha256: str) -> Path:
    return vault.objects_dir() / sha256[:2] / sha256


def exists(sha256: str) -> bool:
    return _path_for(sha256).exists()


def path_of(sha256: str) -> Path:
    return _path_for(sha256)


def copy_in(source: Path) -> tuple[str, Path]:
    """Copies a file in under the hash of what was copied, so an edit partway can't
    store the wrong bytes. A file already held costs nothing; a crash leaves no
    half-written object."""
    vault.objects_dir().mkdir(parents=True, exist_ok=True)
    staging = vault.objects_dir() / f"incoming-{uuid.uuid4().hex}"
    sha256 = originals.copy_hashed(source, staging)
    target = _path_for(sha256)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(target)
    return sha256, target


def read(sha256: str) -> bytes:
    return _path_for(sha256).read_bytes()


def move_to_trash(sha256: str) -> None:
    target = _path_for(sha256)
    if target.exists():
        macos.move_to_trash(target)
