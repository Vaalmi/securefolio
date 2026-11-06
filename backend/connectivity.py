"""Whether a provider is reachable: a content-free connection with a 1 s timeout,
cached for 30 s per host, and never tried while Offline is on."""

import socket
import time
from urllib.parse import urlsplit

from backend import build_mode, providers, settings

PORT = 443
TIMEOUT_SECONDS = 1.0
CACHE_SECONDS = 30.0
_cached: dict[str, tuple[float, bool]] = {}


def _target(provider: str) -> tuple[str, int] | None:
    """The provider's host, or its stub's in the checks; None: no stub, no real try."""
    stub = build_mode.stub_url(providers.PROVIDERS[provider].stub_env)
    if stub:
        parts = urlsplit(stub)
        return parts.hostname or providers.PROVIDERS[provider].host, parts.port or PORT
    if build_mode.uses_stub_models():
        return None  # the checks' default: no real connection is ever tried
    return providers.PROVIDERS[provider].host, PORT


def _connects(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def reachable(provider: str = providers.ANTHROPIC, wait: bool = True) -> bool:
    """With wait False, what's known is answered at once, and an unknown host counts
    as reachable: the caller asks again, waiting, before anything is sent."""
    if settings.get(settings.OFFLINE):
        return False
    target = _target(provider)
    if target is None:
        return True
    key = f"{target[0]}:{target[1]}"
    now = time.monotonic()
    known = _cached.get(key)
    if known and now - known[0] < CACHE_SECONDS:
        return known[1]
    if not wait:
        return True
    found = _connects(*target)
    _cached[key] = (now, found)
    return found


def forget() -> None:
    """A run that just failed to connect, or the tests, ask again."""
    _cached.clear()
