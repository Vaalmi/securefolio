"""Fails any test that tries to leave the machine, by an audit hook no production
code can replace, and at teardown even if the code caught the refusal."""

import sys
from typing import Any

from backend.netguard import (
    LOOKUP_EVENTS,
    SOCKET_EVENTS,
    is_loopback_address,
    is_loopback_host,
)


class OffMacConnection(AssertionError):
    """A test tried to connect off the machine."""


attempts: list[str] = []
_active: list[bool] = []


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if not _active or not event.startswith("socket."):
        return
    off = (
        event in SOCKET_EVENTS
        and args[1] is not None
        and not is_loopback_address(args[1])
    ) or (event in LOOKUP_EVENTS and not is_loopback_host(args[0]))
    if off:
        attempts.append(f"{event} {args[:2]}")
        raise OffMacConnection(f"{event} {args[:2]}")


sys.addaudithook(_hook)


def activate() -> None:
    attempts.clear()
    _active[:] = [True]


def deactivate() -> list[str]:
    """Stops watching, and returns what was attempted."""
    _active.clear()
    found = list(attempts)
    attempts.clear()
    return found
