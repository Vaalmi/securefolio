"""Claude's citations, read sentence by sentence: a sentence keeps its own citations
when the next sentence cites the same passage."""

from agent import citations


def _element(at: str) -> dict:
    return {"id": at, "text": "x", "label": "Page 1", "locator": {"kind": "pdf"}}


EVIDENCE = [
    {
        "document_id": "d",
        "revision_id": "r",
        "title": "Apartment lease",
        "sensitivity": "standard",
        "elements": [_element("e1"), _element("e2")],
    }
]


def _cites(text: str, *blocks: int) -> dict:
    return {
        "type": "text",
        "text": text,
        "citations": [
            {"search_result_index": 0, "start_block_index": b, "end_block_index": b + 1}
            for b in blocks
        ],
    }


def _each(content: list) -> list[tuple[str, list[int] | None]]:
    built = citations.from_claude(content, EVIDENCE)
    said = [(p["text"].strip(), p.get("cite")) for p in built.parts]
    return [(text, cite) for text, cite in said if text]


def test_a_sentence_keeps_its_citation_when_the_next_cites_the_same_passage():
    # The next block cites a passage the sentence already cited,
    # so nothing new is added, and the sentence lost its own citation.
    content = [
        _cites("The rent is due monthly.", 0),
        _cites(" It is $1,200.", 0),
    ]
    assert _each(content) == [
        ("The rent is due monthly.", [1]),
        ("It is $1,200.", [1]),
    ]


def test_each_sentence_keeps_its_own_when_the_next_adds_another():
    content = [
        _cites("The rent is due monthly.", 0),
        _cites(" It is $1,200.", 0, 1),
    ]
    assert _each(content) == [
        ("The rent is due monthly.", [1]),
        ("It is $1,200.", [1, 2]),
    ]


def test_a_new_passage_alone_still_goes_to_the_next_sentence():
    content = [_cites("The rent is due monthly.", 0), _cites(" It is $1,200.", 1)]
    assert _each(content) == [
        ("The rent is due monthly.", [1]),
        ("It is $1,200.", [2]),
    ]


def test_a_fragment_inside_a_sentence_still_cites_the_whole_sentence():
    content = [
        {"type": "text", "text": "The rent is "},
        _cites("$1,200", 0),
        _cites(" a month, from the lease.", 0),
    ]
    assert _each(content) == [("The rent is $1,200 a month, from the lease.", [1])]
