"""The two per-launch tokens. Neither opens what the other does."""

import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Tokens:
    app: str
    mcp: str


def new_tokens() -> Tokens:
    return Tokens(app=secrets.token_urlsafe(32), mcp=secrets.token_urlsafe(32))


def same(given: str, expected: str) -> bool:
    return secrets.compare_digest(given.encode(), expected.encode())
