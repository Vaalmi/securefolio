"""Offline mode's process-wide guard. An audit hook sees every Python socket
connection, datagram, name lookup and subprocess in this process, whichever library
asks, and refuses the ones that would leave the Mac while Offline is on. Native code
that opens its own sockets isn't seen here: running the packaged app under a sandbox
that denies the network proves there is none."""

import ipaddress
import os
import sys
from collections.abc import Callable
from typing import Any

SOCKET_EVENTS = {"socket.connect", "socket.sendto", "socket.sendmsg"}
LOOKUP_EVENTS = {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}
REVERSE_LOOKUP_EVENT = "socket.getnameinfo"
SPAWN_EVENTS = {
    "subprocess.Popen",
    "os.posix_spawn",
    "os.exec",
    "os.spawn",
    "os.system",
}
WORKER_ARGS = ["-m", "worker.main"]
# What the app itself starts, by exact path and only in forms that can't run another
# command: caffeinate and taskpolicy would otherwise launch whatever they're given.
ALLOWED_TOOLS = {
    "/usr/bin/caffeinate": [["-i", "-w", "PID"]],
    "/usr/bin/pmset": [["-g", "batt"]],
    "/usr/sbin/taskpolicy": [["-c", "utility", "-p", "PID"], ["-b", "-p", "PID"]],
    "/usr/sbin/sysctl": [["-n", "hw.memsize"]],
    "/bin/launchctl": [["getenv", "OLLAMA_NO_CLOUD"]],
}
LOOPBACK_NAMES = {"localhost", "ip6-localhost"}


class OfflineRefused(ConnectionError):
    """A connection off the Mac, attempted while Offline mode is on."""


def _never() -> bool:
    return False


_guard: dict[str, Callable[[], bool]] = {"is_offline": _never}
_installed: list[bool] = []


def is_loopback_host(host: Any) -> bool:
    if host is None or host == "":
        return True
    name = host.decode() if isinstance(host, bytes | bytearray) else str(host)
    if name in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name.split("%")[0]).is_loopback
    except ValueError:
        return False


def is_loopback_address(address: Any) -> bool:
    if isinstance(address, str | bytes):  # a Unix socket's path
        return True
    return is_loopback_host(address[0])


def leaves_the_mac(event: str, args: tuple[Any, ...]) -> str | None:
    """What this audit event would reach off the Mac, or None if it stays on it."""
    if event in SOCKET_EVENTS:
        if args[1] is not None and not is_loopback_address(args[1]):
            return f"{event} to {args[1]}"
    elif event in LOOKUP_EVENTS:
        if not is_loopback_host(args[0]):
            return f"{event} of {args[0]}"
    elif event == REVERSE_LOOKUP_EVENT:
        if not is_loopback_address(args[0]):
            return f"{event} of {args[0]}"
    elif event in SPAWN_EVENTS and not _is_allowed_spawn(event, args):
        return f"{event} of {os.path.basename(str(args[0]).split()[0])}"
    return None


def _is_allowed_spawn(event: str, args: tuple[Any, ...]) -> bool:
    """The worker, which installs this guard itself first thing, and the system tools
    in their allowed forms. Any other interpreter wouldn't inherit the guard."""
    if event != "subprocess.Popen":
        return False
    program, arguments = str(args[0]), [str(a) for a in args[1] or []]
    if program == sys.executable:
        return arguments[1:] == WORKER_ARGS
    return any(
        _same_form(arguments[1:], form) for form in ALLOWED_TOOLS.get(program, [])
    )


def _same_form(arguments: list[str], form: list[str]) -> bool:
    return len(arguments) == len(form) and all(
        given.isdigit() if expected == "PID" else given == expected
        for given, expected in zip(arguments, form, strict=True)
    )


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if event.startswith(("socket.", "subprocess.", "os.")):
        target = leaves_the_mac(event, args)
        if target is not None and _guard["is_offline"]():
            raise OfflineRefused(f"Offline mode is on; refused {target}.")


def install(is_offline: Callable[[], bool]) -> None:
    """Guards this process from now on, asking `is_offline` at each attempt."""
    _guard["is_offline"] = is_offline
    if not _installed:
        sys.addaudithook(_hook)
        _installed.append(True)
