"""An answer in words, its marks carried in words: the one place Copy, a note, the saved
content and a follow-up get it."""

from agent import plain

UNSHOWN = {"text": "$28,200", "kind": "money", "start": 31, "end": 38}
CITED = {
    "text": "The rent is $2,350 a month, or $28,200 a year. ",
    "cite": [1],
    "figures": [
        {"text": "$2,350", "kind": "money", "start": 12, "end": 18, "state": "shown"},
        {**UNSHOWN, "state": "unshown"},
    ],
}
UNCITED = {"text": "It was raised last year. ", "uncited": True}
ANSWER = {
    "kind": "answer",
    "parts": [CITED, UNCITED],
    "citations": [{"n": 1, "title": "Lease", "label": "Page 1"}],
}


def test_a_note_and_a_follow_up_read_the_marks_in_words():
    assert plain.text_of(ANSWER) == (
        "The rent is $2,350 a month, or $28,200 (not in its sources) a year. "
        "It was raised last year. (uncited) "
    )


def test_copy_carries_markers_marks_sources_and_what_the_marks_mean():
    assert plain.copy_of(ANSWER).split("\n") == [
        "The rent is $2,350 a month, or $28,200 (not in its sources) a year.[1] "
        "It was raised last year. (uncited) ",
        "[1] Lease, Page 1",
        plain.UNSHOWN_LEGEND,
        plain.UNCITED_LEGEND,
    ]


def test_an_answer_with_no_marks_copies_as_it_always_did():
    answer = {
        "kind": "answer",
        "parts": [{"text": "The rent is due monthly. ", "cite": [1]}],
        "citations": [{"n": 1, "title": "Lease", "label": "Page 1"}],
    }
    assert plain.copy_of(answer) == "The rent is due monthly.[1] \n[1] Lease, Page 1"
    assert plain.text_of(answer) == "The rent is due monthly. "


def test_a_list_keeps_its_rows_and_total():
    answer = {
        "kind": "list",
        "parts": [{"text": "Two policies. ", "cite": [1]}],
        "citations": [],
        "table": {
            "rows": [
                {"title": "Auto", "doc_date": "2025-01-01", "amount_text": "$989.60"},
                {"title": "Renters", "doc_date": None, "amount_text": "$675.67"},
            ],
            "total": 1665.27,
            "wants_total": True,
        },
    }
    assert plain.text_of(answer).split("\n") == [
        "Two policies. ",
        "Auto, 2025-01-01: $989.60",
        "Renters, no date: $675.67",
        "Total: $1,665.27",
    ]


def test_a_count_is_saved_as_its_words_and_copied_as_it_reads():
    counted = {"kind": "answer", "counted": True, "parts": [{"text": "12 documents."}]}
    assert plain.text_of(counted) == plain.COUNTED_WORDS
    assert plain.copy_of(counted) == "12 documents."
