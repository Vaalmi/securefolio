"""What counts as a hit, and as a correct answer."""

from evals import run, score


def item(title: str, text: str, page: int | None = None) -> dict:
    return {
        "title": title,
        "text": text,
        "elements": [{"text": text, "locator": {"page": page} if page else {}}],
    }


FACT = {
    "documents": [],
    "passage_facts": {},
    "expected_document": "Home/Lease.pdf",
    "expected_page": 3,
    "answer": "$299.11",
    "category": "fact",
}
PAIR = {
    "category": "multi",
    "documents": ["A/One.pdf", "A/Two.pdf"],
    "passage_facts": {"A/One.pdf": "$10.00", "A/Two.pdf": "$30.00"},
    "answer_facts": ["$20.00"],
    "expected_document": None,
    "expected_page": None,
    "answer": None,
}


def test_a_hit_needs_the_answer_on_its_page():
    assert score.hit(FACT, [item("Lease", "It is $299.11.", 3)], 10) == 1
    assert score.hit(FACT, [item("Lease", "It is $299.11.", 2)], 10) is None
    assert score.hit(FACT, [item("Lease", "Boilerplate.", 3)], 10) is None


def test_across_documents_a_cover_page_isnt_a_hit():
    covers = [item("One", "Cover."), item("Two", "Cover.")]
    assert score.hit(PAIR, covers, 10) is None


def test_across_documents_the_rank_is_the_last_fact_found():
    ranked = [
        item("One", "It is $10.00."),
        item("Other", "Nothing."),
        item("Two", "It is $30.00."),
    ]
    assert score.hit(PAIR, ranked, 10) == 3


def answer(text: str, *titles: str) -> dict:
    return {
        "parts": [{"text": text}],
        "citations": [{"title": title} for title in titles],
    }


def test_citing_both_documents_with_a_wrong_figure_is_wrong():
    wrong = answer("Two costs $99.00 more.", "One", "Two")
    assert run._has_the_facts(PAIR, wrong) is False  # noqa: SLF001
    right = answer("Two costs $20.00 more.", "One", "Two")
    assert run._has_the_facts(PAIR, right) is True  # noqa: SLF001


def test_the_right_figure_needs_the_right_document():
    elsewhere = answer("It is $299.11.", "Other")
    assert run._has_the_facts(FACT, elsewhere) is False  # noqa: SLF001
    assert run._has_the_facts(FACT, answer("It is $299.11.", "Lease")) is True  # noqa: SLF001


def test_a_list_counts_its_table():
    total = {**PAIR, "documents": ["A/One.pdf"], "answer_facts": ["$45.00"]}
    listed = {
        "parts": [],
        "citations": [],
        "table": {
            "rows": [{"title": "One", "amount_text": "$45.00"}],
            "wants_total": True,
            "total": 45.0,
        },
    }
    assert run._has_the_facts(total, listed) is True  # noqa: SLF001


def test_an_answer_spanning_elements_on_the_wrong_page_is_no_hit():
    spanning = {
        "title": "Lease",
        "text": "It is $299.11.",
        "elements": [
            {"text": "It is", "locator": {"page": 2}},
            {"text": "$299.11.", "locator": {"page": 2}},
        ],
    }
    assert score.hit(FACT, [spanning], 10) is None
    spanning["elements"][1]["locator"] = {"page": 3}
    assert score.hit(FACT, [spanning], 10) == 1


def test_a_false_sentence_in_a_list_counts_against_it(monkeypatch):
    monkeypatch.setattr(run.models, "judge_sentence", lambda *_given, **_named: False)
    listed = {
        "thread_id": "t1",
        "parts": [{"text": "These cost more than last year.", "uncited": True}],
        "citations": [],
        "table": {
            "rows": [
                {
                    "title": "One",
                    "amount_text": "$45.00",
                    "summary": "A policy.",
                    "document_id": "d1",
                    "sensitivity": "standard",
                }
            ]
        },
    }
    assert run._judged(listed)[2] is False  # noqa: SLF001


def test_the_judge_is_told_the_answers_thread(monkeypatch):
    seen = []

    def records(_sentence, _target, _siblings, thread, **_named):
        seen.append(thread)
        return True

    monkeypatch.setattr(run.models, "judge_citation", records)
    monkeypatch.setattr(run, "_passage", lambda _c: "Words.")
    run._judged(  # noqa: SLF001
        {
            "thread_id": "t9",
            "parts": [{"text": "A claim.", "cite": [1]}],
            "citations": [
                {
                    "n": 1,
                    "title": "T",
                    "quote": "Q",
                    "document_id": "d1",
                    "sensitivity": "standard",
                }
            ],
        }
    )
    assert seen == ["t9"]


def test_private_evidence_never_reaches_the_judge(monkeypatch):
    called = []

    def refuses(*_given, **_named):
        called.append(True)
        raise run.models.CloudRefused("Private")

    monkeypatch.setattr(run.models, "judge_citation", refuses)
    monkeypatch.setattr(run, "_passage", lambda _c: "The refund.")
    found = {
        "parts": [{"text": "The refund is $5.", "cite": [1]}],
        "citations": [
            {
                "n": 1,
                "title": "Return",
                "quote": "Refund $5.",
                "document_id": "d1",
                "sensitivity": "private",
            }
        ],
    }
    claims, verdicts, sound = run._judged(found)  # noqa: SLF001
    assert claims == [
        {"claim": "The refund is $5.", "title": "Return", "by_hand": True}
    ]
    assert verdicts == []
    assert sound is True
