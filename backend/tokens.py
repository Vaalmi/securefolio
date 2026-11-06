"""Counts tokens with the embedder's own tokenizer, which needs no model weights.
In the tests and before the download, a stand-in counts every word, number run and
punctuation mark, and long runs by their length."""

import importlib
import re
from functools import lru_cache
from typing import Any

from backend import build_mode, embedder

RUN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
LONG_RUN = 4


@lru_cache(maxsize=1)
def _tokenizer() -> Any:
    transformers = importlib.import_module("transformers")
    return transformers.AutoTokenizer.from_pretrained(embedder.MODEL)


def _stand_in(text: str) -> int:
    return 1 + sum(len(run) // LONG_RUN + 1 for run in RUN.findall(text))


def count(text: str) -> int:
    if build_mode.uses_stub_models():
        return _stand_in(text)
    try:
        return len(_tokenizer().encode(text, add_special_tokens=False))
    except OSError:  # the tokenizer isn't downloaded yet
        return _stand_in(text)
