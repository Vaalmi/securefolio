"""The only code that runs the MLX page-reading model, with a stand-in off Apple
Silicon and in the tests. Granite-Docling reads one page's image into DocTags
through Docling's VLM pipeline, and gives each element a box."""

import gc
import importlib
import platform
import sys
import time
from functools import lru_cache
from io import BytesIO
from typing import Any, Protocol


class PageRead(Protocol):
    def __call__(self, pdf: bytes, page: int, /) -> Any: ...


_stand_in: list[PageRead] = []
_last_used: list[float] = []


def use_stand_in(reader: PageRead | None) -> None:
    _stand_in[:] = [reader] if reader else []


def available() -> bool:
    on_apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
    return bool(_stand_in) or on_apple_silicon


@lru_cache(maxsize=1)
def _converter() -> Any:
    # Docling is imported only when a page is read: it takes seconds and much memory.
    engine = importlib.import_module("worker.docling_engine")
    return engine.page_reading_converter()


def read_page(pdf: bytes, page: int) -> Any:
    """A DoclingDocument of one page, read again from how it looks, or None when
    the reading failed."""
    if _stand_in:
        return _stand_in[0](pdf, page)
    _last_used[:] = [time.monotonic()]
    engine = importlib.import_module("worker.docling_engine")
    source = engine.stream_of("page.pdf", BytesIO(pdf))
    return engine.read_or_none(_converter(), source, page)


def unload_if_idle(now: float, idle_seconds: float) -> None:
    if _last_used and now - _last_used[0] >= idle_seconds:
        unload()


def unload() -> None:
    """Frees the model before the worker embeds the new revision."""
    _last_used.clear()
    _converter.cache_clear()
    gc.collect()
    if sys.platform == "darwin":
        importlib.import_module("mlx.core").clear_cache()
