"""Each macOS-only call sits behind backend/macos.py, with a stand-in off macOS, so the
checks pass on Linux."""

import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from backend import macos, model_store, netguard

ROOT = Path(__file__).parents[2]
PRODUCT = ["agent", "backend", "worker"]


def test_only_the_macos_module_starts_a_macos_tool():
    starters = {
        str(path.relative_to(ROOT))
        for folder in PRODUCT
        for path in (ROOT / folder).rglob("*.py")
        if "tests" not in path.parts
        and any(tool in path.read_text() for tool in netguard.ALLOWED_TOOLS)
    }
    assert starters <= {"backend/macos.py", "backend/netguard.py"}


@pytest.fixture
def off_macos(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a macOS tool was started off macOS")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


@pytest.mark.usefixtures("off_macos")
def test_off_macos_each_call_has_its_stand_in():
    assert model_store.no_cloud_is_set() is False
    assert model_store.memory_gib() == psutil.virtual_memory().total // model_store.GIB
    assert macos.hold_sleep_assertion(1) is None
    macos.lower_priority(1)
