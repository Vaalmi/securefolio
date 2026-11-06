"""Qwen3-Embedding in-process, in float16, loaded when needed and unloaded after 5
idle minutes. In the tests and off Apple Silicon, a deterministic stand-in: shared
words give similar vectors, which is all the tests need of meaning."""

import hashlib
import importlib
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from backend import build_mode

MODEL = "Qwen/Qwen3-Embedding-0.6B"
DIMENSION = 1024
TRUNCATION = 8192
BATCH = 16
IDLE_SECONDS = 300
QUERY_INSTRUCTION = "query"
WORD = re.compile(r"\w+")


@dataclass
class _Loaded:
    model: Any = None
    used_at: float = 0.0


_state = _Loaded()
_lock = threading.Lock()
_loading = threading.Event()


def describe() -> dict[str, Any]:
    """What an index generation records about the model."""
    return {
        "model": "stand-in" if build_mode.uses_stub_models() else MODEL,
        "model_revision": "main",
        "query_instruction": QUERY_INSTRUCTION,
        "document_instruction": "",
        "dimension": DIMENSION,
        "metric": "cosine",
        "truncation": TRUNCATION,
    }


def _stand_in(texts: list[str]) -> np.ndarray:
    vectors = np.zeros((len(texts), DIMENSION), dtype=np.float32)
    for row, text in enumerate(texts):
        for word in WORD.findall(text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vectors[row, int.from_bytes(digest[:4], "big") % DIMENSION] += 1.0
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


def _load() -> Any:
    transformers = importlib.import_module("sentence_transformers")
    torch = importlib.import_module("torch")
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    half = {"dtype": torch.float16} if device == "mps" else {}
    return transformers.SentenceTransformer(MODEL, device=device, model_kwargs=half)


def _model() -> Any:
    with _lock:
        if _state.model is None:
            _state.model = _load()
        _state.used_at = time.monotonic()
        return _state.model


def is_ready() -> bool:
    return build_mode.uses_stub_models() or _state.model is not None


def start_loading() -> None:
    """Warms the model in the background; a search box's focus calls this."""
    if is_ready() or _loading.is_set():
        return
    _loading.set()

    def load() -> None:
        try:
            _model()
        finally:
            _loading.clear()

    threading.Thread(target=load, daemon=True).start()


def _encode(texts: list[str], prompt_name: str | None) -> np.ndarray:
    if build_mode.uses_stub_models():
        return _stand_in(texts)
    vectors = _model().encode(
        texts, batch_size=BATCH, normalize_embeddings=True, prompt_name=prompt_name
    )
    clear_metal_cache()
    return np.asarray(vectors, dtype=np.float32)


def clear_metal_cache() -> None:
    torch = importlib.import_module("torch")
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


def embed_documents(texts: list[str]) -> np.ndarray:
    return _encode(texts, None)


def embed_query(text: str) -> np.ndarray:
    return _encode([text], QUERY_INSTRUCTION)[0]


def to_blob(vector: np.ndarray) -> bytes:
    return vector.astype(np.float16).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)


def unload() -> None:
    with _lock:
        had_model = _state.model is not None
        _state.model = None
    if had_model:
        clear_metal_cache()


def unload_if_idle(now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    if _state.model is not None and now - _state.used_at >= IDLE_SECONDS:
        unload()
        return True
    return False
