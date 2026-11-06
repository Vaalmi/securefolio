"""Keeps the two processes' models under 6 GB together. While an Ask's reranker is
loaded in the backend, the worker holds its embedder back and frees it."""

import os

import psutil

from backend import settings

RERANKER_LOADED = "reranker_loaded"


def set_reranker_loaded(loaded: bool) -> None:
    """Holds the owner's pid, so a process that died doesn't hold the worker back."""
    settings.put(RERANKER_LOADED, os.getpid() if loaded else None)


def reranker_loaded() -> bool:
    owner = settings.get(RERANKER_LOADED)
    return bool(owner) and psutil.pid_exists(int(owner))
