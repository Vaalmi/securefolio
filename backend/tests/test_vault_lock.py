"""A second backend takes over from a live one through the owner's own socket."""

import os
import subprocess
import sys
import threading

import pytest

from backend import vault, vault_lock

HOLDER = """
import signal, sys, time
from backend import vault_lock
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
held = vault_lock.acquire()
print("held", flush=True)
time.sleep(60)
"""
STUCK = """
import os, signal, time
from backend import vault_lock
signal.signal(signal.SIGTERM, signal.SIG_IGN)
vault_lock.SELF_STOP_SECONDS = 0.2
held = vault_lock.acquire()
print("held", flush=True)
time.sleep(60)
"""


def start(code: str) -> subprocess.Popen:
    holder = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=os.environ
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_no_process_is_signalled_when_the_vault_is_free(monkeypatch):
    signalled = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append(pid))
    held = vault_lock.acquire()
    assert signalled == []
    held.release()
    assert not vault.control_socket().exists()


def test_a_stale_socket_file_does_no_harm():
    vault.control_socket().write_text("left behind")
    vault_lock._ask_owner_to_stop()
    held = vault_lock.acquire()
    held.release()


def test_a_live_backend_stops_when_asked_and_the_vault_changes_hands():
    holder = start(HOLDER)
    held = vault_lock.acquire()
    assert holder.wait(timeout=5) == 0
    held.release()


def test_a_stuck_backend_ends_itself():
    holder = start(STUCK)
    held = vault_lock.acquire()
    assert holder.wait(timeout=5) == 1
    held.release()


def test_a_holder_that_exits_between_the_probe_and_the_request_is_fine():
    holder = start(HOLDER)
    holder.terminate()
    holder.wait(timeout=5)
    vault_lock._ask_owner_to_stop()  # nobody answers, and nothing is raised
    held = vault_lock.acquire()
    held.release()


def test_giving_up_says_so(monkeypatch):
    monkeypatch.setattr(vault_lock, "TAKEOVER_SECONDS", 0.2)
    monkeypatch.setattr(vault_lock, "_ask_owner_to_stop", lambda: None)
    other = open(vault.lock_file(), "a+", encoding="utf-8")  # noqa: SIM115
    assert vault_lock._try_lock(other)
    with pytest.raises(TimeoutError):
        vault_lock.acquire()
    other.close()


def test_only_the_stop_word_stops_it(monkeypatch):
    stops = []
    monkeypatch.setattr(vault_lock, "_stop_this_process", lambda: stops.append(1))
    listener = vault_lock._listen()
    thread = threading.Thread(target=vault_lock._serve, args=(listener,), daemon=True)
    thread.start()
    vault_lock._ask_owner_to_stop()
    thread.join(0.2)
    listener.close()
    assert stops == [1]
