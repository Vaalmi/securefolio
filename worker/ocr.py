"""The only OCR: Apple Vision through ocrmac, with a stand-in off macOS and in the
tests. Boxes come back normalized to [0..1] from the top left."""

import importlib
import sys
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class Word:
    text: str
    confidence: float
    box: tuple[float, float, float, float]  # x0, y0, x1, y1


class Reader(Protocol):
    def __call__(self, image: bytes, /) -> list[Word]: ...


def _vision_read(image: bytes) -> list[Word]:
    ocrmac = importlib.import_module("ocrmac.ocrmac")
    picture = Image.open(BytesIO(image))
    found = ocrmac.OCR(picture, unit="token", recognition_level="accurate").recognize()
    words = []
    for text, confidence, (x, y, width, height) in found:
        # Vision's origin is the bottom left, and it reads whole lines.
        words += _split_line(text, confidence, (x, 1 - y - height, x + width, 1 - y))
    return words


def _split_line(
    text: str, confidence: float, box: tuple[float, float, float, float]
) -> list[Word]:
    """Each word of a line, with its share of the line's width by characters."""
    x0, y0, x1, y1 = box
    per_character = (x1 - x0) / max(len(text), 1)
    words = []
    start = 0
    for word in text.split():
        start = text.index(word, start)
        left = x0 + start * per_character
        words.append(
            Word(word, confidence, (left, y0, left + len(word) * per_character, y1))
        )
        start += len(word)
    return words


_stand_in: list[Reader] = []


def use_stand_in(reader: Reader | None) -> None:
    _stand_in[:] = [reader] if reader else []


def read_words(image: bytes) -> list[Word]:
    if _stand_in:
        return _stand_in[0](image)
    if sys.platform != "darwin":
        return []
    return _vision_read(image)
