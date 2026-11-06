"""Each macOS-only call behind one function, with a stand-in off macOS and in the
tests. Nothing else calls these system facilities."""

import os
import stat
import subprocess
import sys
from pathlib import Path

import psutil
from send2trash import send2trash

from backend import build_mode

SF_DATALESS = 0x40000000
_dataless_paths: set[str] = set()


def use_dataless_stand_in(path: str | Path) -> None:
    """Marks a path as not downloaded, for the tests and the seeded placeholder."""
    _dataless_paths.add(str(path))


def clear_dataless_stand_in(path: str | Path) -> None:
    _dataless_paths.discard(str(path))


def _named_placeholders() -> set[str]:
    """Paths a development vault treats as not downloaded; never in a release build."""
    if build_mode.is_release():
        return set()
    return set(
        filter(None, os.environ.get(build_mode.DATALESS_ENV, "").split(os.pathsep))
    )


def is_dataless(path: Path, mode_flags: int | None = None) -> bool:
    if str(path) in _dataless_paths or str(path) in _named_placeholders():
        return True
    flags = (
        mode_flags if mode_flags is not None else getattr(path.stat(), "st_flags", 0)
    )
    return bool(flags & SF_DATALESS)


def is_regular_file(mode: int) -> bool:
    return stat.S_ISREG(mode)


def move_to_trash(path: Path) -> None:
    send2trash(path)


def on_power_or_enough_battery(minimum_percent: int = 20) -> bool:
    battery = psutil.sensors_battery()
    if battery is None or battery.power_plugged:
        return True
    return battery.percent > minimum_percent


def on_power() -> bool:
    battery = psutil.sensors_battery()
    return battery is None or bool(battery.power_plugged)


def lower_priority(pid: int) -> None:
    """Utility priority for the worker."""
    if sys.platform == "darwin":
        command = ["/usr/sbin/taskpolicy", "-c", "utility", "-p", str(pid)]
        subprocess.run(command, check=False, capture_output=True)  # noqa: S603


def hold_sleep_assertion(pid: int) -> subprocess.Popen | None:
    """Keeps the Mac awake while `pid` lives, or until the returned process stops."""
    if sys.platform != "darwin":
        return None
    command = ["/usr/bin/caffeinate", "-i", "-w", str(pid)]
    return subprocess.Popen(command)  # noqa: S603


def memory_bytes() -> int:
    """This Mac's memory. Off macOS, where sysctl has no such key, psutil's figure."""
    if sys.platform != "darwin":
        return psutil.virtual_memory().total
    command = ["/usr/sbin/sysctl", "-n", "hw.memsize"]
    found = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    return int(found.stdout.strip() or 0)


def ollama_no_cloud() -> bool:
    """Whether launchd gives the Ollama app OLLAMA_NO_CLOUD=1. Off macOS there's no
    launchd to ask, so it reads as not set."""
    if sys.platform != "darwin":
        return False
    command = ["/bin/launchctl", "getenv", "OLLAMA_NO_CLOUD"]
    found = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    return found.stdout.strip() == "1"


def open_in_background_download(path: Path) -> None:
    """Asks the file provider for a not-downloaded file by reading its first byte."""
    with path.open("rb") as file:
        file.read(1)
