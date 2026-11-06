"""Where the vault's files are. Only this module knows the paths."""

import hashlib
import os
import tempfile
from pathlib import Path

from backend import telemetry

VAULT_ENV = "SECUREFOLIO_VAULT"
DEFAULT_VAULT = Path.home() / "Library" / "Application Support" / "SecureFolio"
# SecureFolio was called DocSynth. A development .env may still name its vault by the
# old variable, which must never fall through to the real library; and the first
# start of the app moves DocSynth's library folder to SecureFolio's (vault_move.py).
LEGACY_VAULT_ENV = "DOCSYNTH_VAULT"
LEGACY_VAULT = Path.home() / "Library" / "Application Support" / "DocSynth"
LOCK_NAME = "backend.lock"


def configured() -> str | None:
    """The vault the environment names, in development and the tests, if any."""
    return os.environ.get(VAULT_ENV) or os.environ.get(LEGACY_VAULT_ENV)


def root() -> Path:
    chosen = configured()
    path = Path(chosen) if chosen else DEFAULT_VAULT
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_db() -> Path:
    return root() / "library.db"


def lock_file() -> Path:
    return root() / LOCK_NAME


def control_socket() -> Path:
    """In this user's private temp folder: a Unix socket's path has a short limit."""
    name = hashlib.sha256(str(root().resolve()).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"securefolio-{name}.sock"


def runtime_file() -> Path:
    return root() / "runtime.json"


def index_dir() -> Path:
    return root() / "index.lance"


OBJECTS_NAME = "objects"


def objects_dir() -> Path:
    return root() / OBJECTS_NAME


def models_dir() -> Path:
    return root() / "models"


def backups_dir() -> Path:
    return root() / "backups"


def logs_dir() -> Path:
    return root() / "logs"


def tombstone_ledger() -> Path:
    return root() / "tombstones.json"


def model_cache() -> Path:
    cache = os.environ.get("SECUREFOLIO_MODEL_CACHE")
    return Path(cache) if cache else models_dir()


def use_model_cache() -> None:
    """Turns telemetry and tracing off, then points Hugging Face and Docling at the
    vault's model cache. Every process that runs a model starts here. Nothing here
    reaches for Hugging Face's servers: model_store.py does the downloading."""
    telemetry.turn_off()
    models = model_cache()
    models.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HOME"] = str(models / "huggingface")
    docling = models / "docling"
    if docling.is_dir() and any(docling.iterdir()):
        os.environ["DOCLING_ARTIFACTS_PATH"] = str(docling)
