"""Fixtures for every Python test. No test reaches a service off the machine,
reads .env or calls a real model, and each runs on a vault of its own."""

import pytest
from docling_core.types.doc.document import DoclingDocument
from fastapi.testclient import TestClient

from backend import auth, model_store, netguard, sources, vault
from backend.build_mode import DEV_ORIGINS_ENV, STUB_MODELS_ENV
from backend.main import NO_WORKER_ENV, create_app
from backend.migrations import run as migrations
from testing import network, recorded
from worker import ocr, page_reader, parse

TEST_PORT = 18765
TEST_TOKENS = auth.Tokens(app="test-app-token", mcp="test-mcp-token")
LOOPBACK = f"http://127.0.0.1:{TEST_PORT}"


@pytest.fixture(autouse=True)
def test_vault(tmp_path, monkeypatch):
    """A temporary vault, stub models, and none of the shell's settings."""
    monkeypatch.setenv(vault.VAULT_ENV, str(tmp_path / "vault"))
    monkeypatch.setenv(NO_WORKER_ENV, "1")
    monkeypatch.setenv(STUB_MODELS_ENV, "1")
    monkeypatch.delenv(DEV_ORIGINS_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    # Ollama's last list is kept per process; each test starts with it not yet asked.
    monkeypatch.setattr(
        model_store, "_ollama", {"at": None, "models": [], "running": True}
    )
    return tmp_path / "vault"


@pytest.fixture(autouse=True)
def no_network_off_the_mac():
    """Fails any test that tried to leave the machine, even if the code caught it."""
    network.activate()
    yield
    # The app's guard outlives its lifespan, and reads the settings of a vault now gone.
    netguard.install(lambda: False)
    assert network.deactivate() == []


@pytest.fixture
def library():
    """A migrated vault with the built-in source, and Docling's models stood in for."""
    migrations.upgrade()
    sources.ensure_library_source()
    parse.use_recorded(recorded.convert)
    ocr.use_stand_in(lambda _image: [])
    page_reader.use_stand_in(lambda _pdf, _page: DoclingDocument(name="empty"))
    yield
    parse.use_recorded(None)
    ocr.use_stand_in(None)
    page_reader.use_stand_in(None)


@pytest.fixture
def app_client():
    """The backend as the app calls it, with the app's token."""
    app = create_app(TEST_PORT, TEST_TOKENS, write_runtime=False)
    headers = {"Authorization": f"Bearer {TEST_TOKENS.app}"}
    with TestClient(app, base_url=LOOPBACK, headers=headers) as client:
        yield client


@pytest.fixture
def bare_client():
    """The backend with no token on the requests."""
    app = create_app(TEST_PORT, TEST_TOKENS, write_runtime=False)
    with TestClient(app, base_url=LOOPBACK) as client:
        yield client
