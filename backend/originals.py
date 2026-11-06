"""The only code that opens a user's file, and it opens it read-only."""

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from backend import macos

CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Seen:
    path: Path
    inode: int
    size: int
    mtime: float
    dataless: bool


def look(path: Path) -> Seen | None:
    """What a stat shows, reading nothing; None for anything but a regular file."""
    try:
        status = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(status.st_mode):
        return None
    flags = getattr(status, "st_flags", 0)
    return Seen(
        path,
        status.st_ino,
        status.st_size,
        status.st_mtime,
        macos.is_dataless(path, flags),
    )


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def read_bytes(path: Path) -> bytes:
    with path.open("rb") as file:
        return file.read()


def copy_hashed(path: Path, destination: Path) -> str:
    """Copies a file, returning the hash of the bytes actually copied."""
    digest = hashlib.sha256()
    with path.open("rb") as source, destination.open("wb") as target:
        while block := source.read(CHUNK):
            digest.update(block)
            target.write(block)
    return digest.hexdigest()


def materialize(path: Path) -> None:
    """Asks the file provider for a not-downloaded file, when the owner chooses."""
    macos.open_in_background_download(path)


def walk(root: Path) -> list[Path]:
    """Every file under a folder, not following links out of it."""
    found: list[Path] = []
    for folder, subfolders, names in os.walk(root):
        subfolders[:] = [name for name in subfolders if not name.startswith(".")]
        found += [Path(folder) / name for name in names]
    return found
