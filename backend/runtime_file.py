"""Writes and removes runtime.json, which tells the app and the bridge where the
backend is. Only its owner can read it."""

import json
import os

from backend import auth, vault

VERSION = "2.0.0"


def write(port: int, tokens: auth.Tokens) -> None:
    path = vault.runtime_file()
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(
            {
                "port": port,
                "pid": os.getpid(),
                "version": VERSION,
                "app_token": tokens.app,
                "mcp_token": tokens.mcp,
            },
            file,
        )


def remove() -> None:
    vault.runtime_file().unlink(missing_ok=True)
