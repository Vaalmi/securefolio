"""Which pages count as hard, by docs/TECH_STACK.md's definitions."""

import math

from worker.hard_pages import (
    PageSignals,
    character_error_rate,
    coverage,
    normalize,
    picture_area,
    text_layer_reasons,
    why_hard,
)
from worker.ocr import Word


def word(text: str, x: float = 0.1, y: float = 0.1) -> Word:
    return Word(text, 1.0, (x, y, x + 0.1, y + 0.02))


def line(*texts: str) -> list[Word]:
    return [word(t, 0.1 + i * 0.1) for i, t in enumerate(texts)]


def test_normalizing_folds_case_joins_hyphens_and_collapses_space():
    assert normalize("Ａ  War-\nranty\tCLAIM") == "a warranty claim"


def test_a_matching_text_layer_isnt_hard():
    words = line("the", "roof", "warranty")
    signals = PageSignals(has_native_text=True, text_cells=words, vision_words=words)
    assert why_hard(signals) == []


def test_a_wrong_text_layer_is_hard():
    signals = PageSignals(
        has_native_text=True,
        text_cells=line("xxx", "yyy", "zzz"),
        vision_words=line("the", "roof", "warranty"),
    )
    assert set(text_layer_reasons(signals)) == {
        "text_layer_differs",
        "text_layer_partial",
    }


def test_a_partial_text_layer_is_hard():
    vision = line("the", "roof", "warranty", "covers", "storm", "damage")
    signals = PageSignals(
        has_native_text=True, text_cells=vision[:2], vision_words=vision
    )
    assert "text_layer_partial" in text_layer_reasons(signals)


def test_text_inside_a_picture_doesnt_count_against_the_layer():
    words = line("roof", "warranty")
    picture_word = word("chartlabel", 0.5, 0.5)
    signals = PageSignals(
        has_native_text=True,
        text_cells=words,
        vision_words=[*words, picture_word],
        picture_boxes=[(0.4, 0.4, 0.8, 0.8)],
    )
    assert text_layer_reasons(signals) == []


def test_a_page_without_a_native_layer_has_nothing_to_compare():
    signals = PageSignals(has_native_text=False, vision_words=line("roof"))
    assert text_layer_reasons(signals) == []


def test_error_rate_is_over_the_vision_length():
    assert character_error_rate("abcd", "abce") == 0.25
    assert character_error_rate("", "") == 0.0


def test_coverage_needs_an_overlapping_box():
    vision = [word("roof", 0.1, 0.1)]
    elsewhere = [word("roof", 0.1, 0.8)]
    assert coverage(vision, vision) == 1.0
    assert coverage(elsewhere, vision) == 0.0
    assert coverage([], []) == 1.0


def test_each_other_reason():
    assert why_hard(PageSignals(mean_grade="fair")) == ["low_confidence"]
    assert why_hard(PageSignals(low_grade="poor")) == ["low_confidence"]
    assert why_hard(PageSignals(mean_grade="good", low_grade="fair")) == []
    assert why_hard(PageSignals(broken_text_layer=True)) == ["broken_text_layer"]
    assert why_hard(PageSignals(tables_empty_share=[0.5])) == ["weak_table"]
    assert why_hard(PageSignals(table_has_span=True)) == ["weak_table"]
    assert why_hard(PageSignals(table_continues=True)) == ["weak_table"]
    assert why_hard(PageSignals(picture_boxes=[(0, 0, 1, 0.6)])) == [
        "pictures_over_half"
    ]
    assert why_hard(PageSignals(empty_or_garbled_after_ocr=True)) == [
        "empty_or_garbled"
    ]
    assert why_hard(PageSignals(owner_asked=True)) == ["owner_asked"]


def test_an_unset_score_is_ignored():
    assert why_hard(PageSignals(tables_empty_share=[math.nan])) == []
    assert why_hard(PageSignals()) == []


def test_clean_tables_arent_hard():
    assert why_hard(PageSignals(tables_empty_share=[0.1])) == []


def test_picture_area_adds_up():
    assert picture_area([(0, 0, 0.5, 0.5), (0.5, 0.5, 1, 1)]) == 0.5
