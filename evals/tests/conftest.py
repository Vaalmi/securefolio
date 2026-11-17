"""Fixtures for the evaluation's tests: both providers stood in for, with keys, and a
copy of the measured-models file so no test writes the real one."""

import shutil

import pytest

from backend import agent_service, keychain, model_catalog, providers, sources, states
from backend.build_mode import STUB_ANTHROPIC_ENV, STUB_OPENAI_ENV
from backend.main import NO_WORKER_ENV
from evals.tests.questions import DATA
from evals.tests.stub_providers import answered, judged
from testing.queue import drain
from testing.stub_models import stub_anthropic, stub_openai


@pytest.fixture
def measured_file(tmp_path, monkeypatch):
    """A copy of backend/model_measured.json, so a test never writes the real one."""
    copy = tmp_path / "model_measured.json"
    shutil.copy(model_catalog.MEASURED, copy)
    monkeypatch.setattr(model_catalog, "MEASURED", copy)
    return copy


@pytest.fixture
def stubs(library, monkeypatch):
    """Both providers' stubs, with keys, and the evaluation's own process settings
    put back afterwards."""
    monkeypatch.setenv(NO_WORKER_ENV, "1")
    monkeypatch.setenv(agent_service.NO_TICKS_ENV, "1")  # as the run sets it
    monkeypatch.setattr(keychain, "get_key", keychain.get_key)
    # A run takes keys from .env for the rest of its process: put back after each.
    monkeypatch.setitem(keychain._from_environment, "on", False)  # noqa: SLF001
    for provider in (providers.ANTHROPIC, providers.OPENAI):
        keychain.set_key(f"test-{provider}-key", provider)
    with stub_anthropic() as anthropic, stub_openai() as openai:
        anthropic.reply = judged
        openai.reply = answered
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, anthropic.url)
        monkeypatch.setenv(STUB_OPENAI_ENV, openai.url)
        yield anthropic, openai
    for provider in (providers.ANTHROPIC, providers.OPENAI):
        keychain.delete_key(provider)


@pytest.fixture
def lease(stubs, tmp_path):
    """The synthetic library's Home folder, indexed, with both providers' stubs."""
    home = tmp_path / "Home"
    shutil.copytree(DATA / "Home", home)
    sources.add_source(str(home), states.STANDARD)
    drain()
    return stubs
