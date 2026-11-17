"""Markers that name several passages, `[1, 2]`, `[1][2]`, `[1-3]` and `[1–3]`, are
read as each of them, before a sentence's stop or after it. Every number is still
read whole, and only passages that exist are named."""

import pytest

from agent import citations

SOURCE = {"document_id": "d", "revision_id": "r", "title": "T", "sensitivity": "s"}


def sources(count: int) -> list[tuple[dict, dict]]:
    return [
        (SOURCE, {"id": f"e{n}", "text": "x", "label": "L", "locator": {}})
        for n in range(1, count + 1)
    ]


def cited(text: str, count: int = 12) -> list[list[str]]:
    """Each sentence's elements, in order."""
    built = citations.from_markers(text, sources(count))
    by_number = {c["n"]: c["element_id"] for c in built.citations}
    return [
        [by_number[n] for n in part.get("cite", [])]
        for part in built.parts
        if part["text"].strip()
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Due monthly [1, 2].",
        "Due monthly [1,2].",
        "Due monthly [1; 2].",
        "Due monthly [1][2].",
        "Due monthly [1] [2].",
        "Due monthly [1-2].",
        "Due monthly [1–2].",
        "Due monthly. [1, 2]",
        "Due monthly. [1-2]",
        "Due monthly. [1][2]",
    ],
)
def test_a_marker_naming_several_passages_cites_each(text):
    assert cited(text) == [["e1", "e2"]]
    built = citations.from_markers(text, sources(12))
    assert citations.plain_text(built) == "Due monthly."


def test_a_range_names_every_passage_in_it():
    assert cited("Paid in full [2-4].") == [["e2", "e3", "e4"]]
    assert cited("Paid in full [10–12].") == [["e10", "e11", "e12"]]
    assert cited("Paid [1, 3-4, 9].") == [["e1", "e3", "e4", "e9"]]


def test_a_list_or_range_names_only_passages_that_exist():
    assert cited("Paid [2, 9].", 3) == [["e2"]], "9 isn't one, 2 still is"
    assert cited("Paid [2-9].", 3) == [[]], "a range past the passages names none"
    assert cited("Paid [3-2].", 3) == [[]], "nor one that runs backwards"
    assert cited("Paid [0-2].", 3) == [[]]
    huge = "Paid [1-" + "9" * 5000 + "]."
    assert cited(huge, 3) == [[]], "a range end too long to read isn't a crash"


def test_ten_is_never_taken_for_one():
    assert cited("A [10, 1].") == [["e10", "e1"]]
    assert cited("A [1-1].") == [["e1"]]
    assert cited("A [10].", 9) == [[]]


def test_brackets_that_arent_markers_stay_as_written():
    built = citations.from_markers("The [sic] figure [1].", sources(2))
    assert citations.plain_text(built) == "The [sic] figure."
    built = citations.from_markers("Pages [1 to 2] [1].", sources(2))
    assert citations.plain_text(built) == "Pages [1 to 2]."


def test_a_replys_shape_counts_a_list_or_range_as_one_marker():
    shape = citations.shape_of("Due [1, 2]. Paid [3-4]. Also [9]. See [sic].", 3)
    assert (shape["markers"], shape["in_range"], shape["other_brackets"]) == (3, 1, 1)
    # [1, 2] names passages; [3-4] doesn't, as 4 isn't one; [9] doesn't.
