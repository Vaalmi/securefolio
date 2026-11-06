"""One backend per vault. The owner holds a lock the OS frees when it dies, and a
Unix socket beside it. A new backend asks to take over through that socket, which
only the live owner can answer, so no pid is ever read or signalled."""

import fcntl
import os
import signal
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from backend import vault

TAKEOVER_SECONDS = 15
SELF_STOP_SECONDS = 10
STOP = b"stop"


@dataclass
class Holding:
    """Keep this for as long as the process owns the vault."""

    lock: IO[str]
    listener: socket.socket

    def release(self) -> None:
        self.listener.close()
        vault.control_socket().unlink(missing_ok=True)
        self.lock.close()


def _try_lock(file: IO[str]) -> bool:
    try:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def try_hold(lock: Path) -> IO[str] | None:
    """The lock file of a vault that isn't this process's own, held, or None while a
    backend holds it. Closing the file lets it go."""
    file = open(lock, "a+", encoding="utf-8")  # noqa: SIM115
    if _try_lock(file):
        return file
    file.close()
    return None


def _ask_owner_to_stop() -> None:
    with socket.socket(socket.AF_UNIX) as client:
        try:
            client.connect(str(vault.control_socket()))
            client.sendall(STOP)
        except OSError:
            pass  # the owner is starting or stopping; the next try will tell


def _stop_this_process() -> None:
    """Stops gracefully; a process too stuck to do that ends itself regardless."""
    timer = threading.Timer(SELF_STOP_SECONDS, os._exit, args=(1,))
    timer.daemon = True
    timer.start()
    os.kill(os.getpid(), signal.SIGTERM)


def _listen() -> socket.socket:
    path = vault.control_socket()
    path.unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    path.chmod(0o600)
    listener.listen(1)
    return listener


def _serve(listener: socket.socket) -> None:
    while True:
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        with connection:
            if connection.recv(len(STOP)) == STOP:
                _stop_this_process()


def acquire(held: IO[str] | None = None) -> Holding:
    """Holds the vault, asking any backend still holding it to stop first. A lock
    this process already holds on the vault (from moving DocSynth's library) is kept
    as it is, never let go in between."""
    file = held or open(vault.lock_file(), "a+", encoding="utf-8")  # noqa: SIM115
    begun = time.monotonic()
    while not _try_lock(file):
        _ask_owner_to_stop()
        if time.monotonic() - begun > TAKEOVER_SECONDS:
            file.close()
            raise TimeoutError(
                "Another SecureFolio backend won't let go of this vault."
            )
        time.sleep(0.1)
    listener = _listen()
    threading.Thread(target=_serve, args=(listener,), daemon=True).start()
    return Holding(file, listener)
