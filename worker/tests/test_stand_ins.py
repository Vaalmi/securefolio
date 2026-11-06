"""The macOS-only readers have stand-ins, so the rest is tested everywhere."""

import pytest

from worker import ocr, page_reader


def test_ocr_uses_its_stand_in_and_splits_lines():
    ocr.use_stand_in(lambda _image: [ocr.Word("hi", 1.0, (0, 0, 1, 1))])
    try:
        assert ocr.read_words(b"x")[0].text == "hi"
    finally:
        ocr.use_stand_in(None)


def test_a_line_splits_into_words_with_their_share_of_the_width():
    words = ocr._split_line("ab cd", 0.9, (0.0, 0.2, 0.5, 0.3))
    assert [w.text for w in words] == ["ab", "cd"]
    assert words[0].box == pytest.approx((0.0, 0.2, 0.2, 0.3))
    assert words[1].box[0] == pytest.approx(0.3)


def test_the_page_reader_uses_its_stand_in():
    page_reader.use_stand_in(lambda _pdf, page: {"page": page})
    try:
        assert page_reader.available()
        assert page_reader.read_page(b"x", 3) == {"page": 3}
    finally:
        page_reader.use_stand_in(None)
