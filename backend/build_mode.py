"""Whether this is a release build, from a marker file only the bundle ships.
No setting can turn a release build into a development one."""

import os
from pathlib import Path

RELEASE_MARKER = Path(__file__).parent / "RELEASE"
DEV_ORIGINS_ENV = "SECUREFOLIO_DEV_ORIGINS"
STUB_ANTHROPIC_ENV = "SECUREFOLIO_STUB_ANTHROPIC_URL"
STUB_OLLAMA_ENV = "SECUREFOLIO_STUB_OLLAMA_URL"
STUB_OPENAI_ENV = "SECUREFOLIO_STUB_OPENAI_URL"
STUB_OPENROUTER_ENV = "SECUREFOLIO_STUB_OPENROUTER_URL"

STUB_MODELS_ENV = "SECUREFOLIO_STUB_MODELS"
DATALESS_ENV = "SECUREFOLIO_DATALESS_PATHS"
WORKER_MODULE_ENV = "SECUREFOLIO_WORKER_MODULE"
WORKER_MODULE = "worker.main"


def is_release() -> bool:
    return RELEASE_MARKER.exists()


def dev_origins() -> set[str]:
    """The Vite origins a development run accepts; none in a release build."""
    if is_release():
        return set()
    return {o for o in os.environ.get(DEV_ORIGINS_ENV, "").split(",") if o}


def stub_url(env_name: str) -> str | None:
    """A stub model server's address, for the checks; never in a release build."""
    if is_release():
        return None
    return os.environ.get(env_name) or None


def uses_stub_models() -> bool:
    return not is_release() and os.environ.get(STUB_MODELS_ENV) == "1"


def worker_module() -> str:
    """The end-to-end tests' worker has stand-in models; never in a release build."""
    if is_release():
        return WORKER_MODULE
    return os.environ.get(WORKER_MODULE_ENV) or WORKER_MODULE
