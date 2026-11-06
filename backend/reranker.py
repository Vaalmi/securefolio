"""Qwen3-Reranker in-process, for Ask only, loaded lazily and unloaded when idle. In
the tests, a stand-in scores by shared words."""

import importlib
import math
import re
import threading
import time
from typing import Any

from backend import build_mode, embedder, model_budget

MODEL = "Qwen/Qwen3-Reranker-0.6B"
BATCH = 4
IDLE_SECONDS = 300
WORD = re.compile(r"\w+")
_lock = threading.Lock()


class _State:
    model: Any = None
    used_at: float = 0.0


_state = _State()


def _load() -> Any:
    transformers = importlib.import_module("sentence_transformers")
    torch = importlib.import_module("torch")
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    half = {"dtype": torch.float16} if device == "mps" else {}
    return transformers.CrossEncoder(MODEL, device=device, model_kwargs=half)


def _stand_in(query: str, texts: list[str]) -> list[float]:
    wanted = {w.casefold() for w in WORD.findall(query)}
    scores = []
    for text in texts:
        words = {w.casefold() for w in WORD.findall(text)}
        scores.append(len(wanted & words) / max(len(wanted), 1))
    return scores


def score(query: str, texts: list[str]) -> list[float]:
    """Higher is closer, in 0..1: the model gives logits, and the stand-in a share."""
    if not texts:
        return []
    if build_mode.uses_stub_models():
        return _stand_in(query, texts)
    with _lock:
        if _state.model is None:
            model_budget.set_reranker_loaded(True)
            try:
                _state.model = _load()
            except BaseException:
                model_budget.set_reranker_loaded(False)  # or the worker waits for good
                raise
        _state.used_at = time.monotonic()
        model = _state.model
    raw = model.predict([(query, text) for text in texts], batch_size=BATCH)
    embedder.clear_metal_cache()
    return [1 / (1 + math.exp(-float(value))) for value in raw]


def unload_if_idle(now: float | None = None) -> bool:
    moment = time.monotonic() if now is None else now
    with _lock:
        if _state.model is None or moment - _state.used_at < IDLE_SECONDS:
            return False
        _state.model = None
    embedder.clear_metal_cache()
    model_budget.set_reranker_loaded(False)
    return True
