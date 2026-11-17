"""The figure check: which figures in a sentence are checked, how the same figure
written two ways is matched, and what counts as shown in a passage."""

from decimal import Decimal

import pytest

from agent import figures
from agent.figures import Passage


def states(sentence: str, passage: str, title: str = "") -> list[tuple[str, str]]:
    found = figures.check_sentence(sentence, {1: Passage(passage, title)})
    return [(f["text"], f["state"]) for f in found]


@pytest.mark.parametrize(
    ("sentence", "passage"),
    [
        ("It ends on 3 March 2025.", "Valid through 2025-03-03."),
        ("It ends on March 3, 2025.", "Valid through 3 March 2025."),
        ("The fee is $1,200.", "Amount: 1200.00"),
        ("You have thirty days.", "within 30 days of notice"),
        ("It runs 12 months.", "a 12-month term"),
        ("Raised $1.2 million.", "a total of $1,200,000"),
        ("Rent is 5% higher.", "an increase of five percent"),
        ("It renews in April 2026.", "Renewal: April 15, 2026"),
        ("It is 36,000 miles.", "or 36000 miles, whichever comes first"),
    ],
)
def test_the_same_figure_written_two_ways_is_shown(sentence: str, passage: str):
    assert [state for _, state in states(sentence, passage)] == ["shown"]


def test_an_amount_keeps_the_currency_its_passage_gives():
    assert states("The fee is $1,200.", "Fee: CAD 1,200") == [("$1,200", "unshown")]


@pytest.mark.parametrize(
    "passage", ["Fee: CAD $1,200", "Fee: $1,200 CAD", "Fee: 1,200 €"]
)
def test_a_currency_around_the_amount_is_kept_too(passage: str):
    """A code before a symbol, a code after the
    amount, or a symbol after it, is that currency, never a dollar amount's."""
    assert states("The fee is $1,200.", passage) == [("$1,200", "unshown")]
    assert states("The fee is $1,200.", "Fee: USD $1,200") == [("$1,200", "shown")]


def test_a_code_before_a_signed_amount_keeps_both():
    """ "CAD -$1,200" is minus 1,200 Canadian dollars."""
    (found,) = figures.figures_in("Adjustment: CAD -$1,200.")
    assert found.value == ("CAD", Decimal("-1200"))
    assert states("The adjustment is -$1,200.", "Adjustment: CAD -$1,200.") == [
        ("-$1,200", "unshown")
    ]
    assert states("It is CAD -$1,200.", "Adjustment: CAD -$1,200.") == [
        ("CAD -$1,200", "shown")
    ]


@pytest.mark.parametrize(
    ("written", "value"),
    [
        ("$-1,200", ("USD", Decimal("-1200"))),
        ("-$1,200", ("USD", Decimal("-1200"))),
        ("CAD -$1,200", ("CAD", Decimal("-1200"))),
        ("CAD $-1,200", ("CAD", Decimal("-1200"))),
        ("USD -1,200", ("USD", Decimal("-1200"))),
        ("$-50", ("USD", Decimal("-50"))),
    ],
)
def test_a_minus_anywhere_among_the_marks_is_the_amounts(written: str, value: tuple):
    """Wherever the minus sits
    among the code and the symbol, the amount keeps its currency and its sign, and a
    small amount is still money, so it's checked."""
    (found,) = figures.figures_in(f"Adjustment: {written}.")
    assert (found.kind, found.value) == ("money", value)


def test_a_signed_amount_shows_only_the_same_currency_and_sign():
    assert states("The adjustment is $-1,200.", "Adjustment: CAD -$1,200.") == [
        ("$-1,200", "unshown")
    ]
    assert states("The adjustment is $-50.", "Adjustment: $50.") == [
        ("$-50", "unshown")
    ]
    assert states("The adjustment is $-50.", "Adjustment: -$50.") == [("$-50", "shown")]


def test_a_symbol_after_the_amount_shows_the_same_amount():
    assert states("The fee is 1,200 €.", "Fee: €1,200") == [("1,200 €", "shown")]


@pytest.mark.parametrize(
    ("words", "value"),
    [
        ("two hundred thirty days", 230),
        ("two thousand five hundred", 2500),
        ("one hundred and twenty-five pages", 125),
    ],
)
def test_a_number_in_several_words_is_one_figure(words: str, value: int):
    """Never split into two figures."""
    (found,) = figures.figures_in(f"It is {words}.")
    assert (found.text, found.value) == (words, Decimal(value))


def test_a_number_in_words_is_shown_by_its_digits():
    assert states("The term is two hundred thirty days.", "The term is 230 days.") == [
        ("two hundred thirty days", "shown")
    ]


def test_an_ambiguous_date_is_shown_by_either_reading():
    assert states("Due 03/04/2025.", "due April 3, 2025") == [("03/04/2025", "shown")]
    assert states("Due 03/04/2025.", "due March 4, 2025") == [("03/04/2025", "shown")]
    assert states("Due 03/04/2025.", "due May 3, 2025") == [("03/04/2025", "unshown")]


def test_a_full_date_isnt_shown_by_its_month_alone():
    assert states("Due March 3, 2025.", "due in March 2025") == [
        ("March 3, 2025", "unshown")
    ]


def test_the_five_kinds_and_what_isnt_checked():
    found = figures.figures_in(
        "On May 2, 2025 it cost $45 for 3 rooms, 12 days, 15%, 1,450 and 36,000 miles."
    )
    kinds = [(f.text, f.kind, figures.checked(f)) for f in found]
    assert kinds == [
        ("May 2, 2025", "date", True),
        ("$45", "money", True),
        ("3", "number", False),  # a small number with no unit isn't checked
        ("12 days", "unit", True),
        ("15%", "percent", True),
        ("1,450", "number", True),
        ("36,000 miles", "number", True),  # 100 or more: a number, unit or not
    ]


def test_units_other_than_currency_arent_checked():
    """The number is matched, its unit isn't."""
    assert states("You have 30 days.", "within 30 weeks") == [("30 days", "shown")]


def test_a_title_shows_a_figure_only_where_the_sentence_names_it():
    lease = "2025 Lease"
    assert states("The lease expires in 2025.", "Rent is due monthly.", lease) == [
        ("2025", "unshown")
    ]
    assert states("The 2025 Lease sets the rent.", "Rent is due monthly.", lease) == [
        ("2025", "shown")
    ]
    assert states(
        "Federal return 2024 shows a refund.", "Refund due.", "Federal return 2024"
    ) == [("2024", "shown")]


def test_a_title_named_in_another_word_order_still_names_it():
    """The count's one wrong mark, in every kept run: q25's "your 2024 federal
    return" names the document called "Federal return 2024"."""
    title = "Federal return 2024"
    assert states(
        "The refund due on your 2024 federal return was $4,561.96.",
        "Refund due $4,561.96.",
        title,
    ) == [("2024", "shown"), ("$4,561.96", "shown")]
    assert states("In 2024 the return was filed.", "Refund due.", title) == [
        ("2024", "unshown")
    ]


def test_a_figure_the_model_worked_out_is_unshown():
    """A sum the passages don't hold is marked: the tooltip says it may have been
    worked out by the model, and that mark is doing its job."""
    assert states("Together they cost $350.", "Item A: $200. Item B: $150.") == [
        ("$350", "unshown")
    ]


def test_a_total_code_worked_out_is_never_marked():
    worked = figures.worked_of({"wants_total": True, "total": 350.0})
    found = figures.check_sentence(
        "Together they cost $350.", {1: Passage("Item A: $200. Item B: $150.")}, worked
    )
    assert [(f["text"], f["state"]) for f in found] == [("$350", "worked")]
    assert figures.worked_of({"wants_total": False, "total": 350.0}) == []


def test_a_worked_total_is_in_dollars_so_another_currency_isnt_it():
    """A list's total is summed in
    dollars, so the same digits in another currency are unshown, not worked out."""
    worked = figures.worked_of({"wants_total": True, "total": 350.0})
    passage = {1: Passage("Item A: $200. Item B: $150.")}
    found = [
        (f["text"], f["state"])
        for sentence in (
            "Together they cost €350.",
            "Together they cost CAD 350.",
            "Together they cost 350 dollars.",
            "Together they cost 350.",
        )
        for f in figures.check_sentence(sentence, passage, worked)
    ]
    assert found == [
        ("€350", "unshown"),
        ("CAD 350", "unshown"),
        ("350 dollars", "worked"),
        ("350", "worked"),
    ]


def test_a_shown_figure_says_where_it_is_when_it_is_there_once():
    passage = "A pet deposit of $299.11 is due. Rent is $1,450."
    (found,) = figures.check_sentence("The deposit is $299.11.", {1: Passage(passage)})
    assert found["cite"] == 1
    assert passage[slice(*found["at"])] == "$299.11"
    assert found["found"] == "$299.11"
    (twice,) = figures.check_sentence(
        "The deposit is $50.", {1: Passage("$50 now and $50 later.")}
    )
    assert twice["state"] == "shown"
    assert twice["at"] is None


def test_the_citation_that_shows_it_is_named():
    cited = {1: Passage("Rent is $1,450."), 2: Passage("Deposit $299.11.")}
    (found,) = figures.check_sentence("The deposit is $299.11.", cited)
    assert found["cite"] == 2


def test_an_answer_checks_its_cited_sentences_only():
    parts = [
        {"text": "The deposit is $299.11. ", "cite": [1]},
        {"text": "It was raised to $400 last year. ", "uncited": True},
        {"text": "Rent is $1,500. ", "cite": [1]},
    ]
    checked = figures.check(parts, {1: Passage("deposit of $299.11 and rent $1,450")})
    assert [[(f["text"], f["state"]) for f in part] for part in checked] == [
        [("$299.11", "shown")],
        [],
        [("$1,500", "unshown")],
    ]


def test_a_sentence_with_a_citation_whose_passage_isnt_known_is_left_unchecked():
    checked = figures.check([{"text": "It is $5.", "cite": [3]}], {})
    assert checked == [[]]
    both = [{"text": "It is $5.", "cite": [1, 3]}]
    assert figures.check(both, {1: Passage("It is $4.")}) == [[]]


def test_a_minus_sign_is_kept():
    """ "-$1,200" doesn't show "$1,200"."""
    assert states("The balance is $1,200.", "Balance: -$1,200.") == [
        ("$1,200", "unshown")
    ]
    assert states("The balance is -$1,200.", "Balance: -$1,200.") == [
        ("-$1,200", "shown")
    ]
    assert states("It fell 5%.", "a change of \u22125%") == [("5%", "unshown")]
    assert states("It is $200.", "from $100-$200") == [("$200", "shown")]
    # A negative figure is checked by its size.
    assert states("The adjustment is -120 days.", "Adjustment: 100 days.") == [
        ("-120 days", "unshown")
    ]
    assert states("It moved -150.", "It moved -150.") == [("-150", "shown")]
    joined = [f.value for f in figures.figures_in("Call 555-0134.")]
    assert joined == [Decimal(555), Decimal(134)], "a hyphen between numbers joins"


def test_numbers_in_words():
    assert figures._number("thirty-five") == Decimal(35)
    assert figures._number("two hundred") == Decimal(200)
    assert figures._number("$5k".lstrip("$")) == Decimal(5000)


def test_a_scale_word_joins_any_number_not_only_money():
    """ "1.2 million people" is 1,200,000,
    so it's checked as any number of 100 or more is, however the passage writes it."""
    (found,) = figures.figures_in("It covers 1.2 million people.")
    assert (found.text, found.kind, found.value) == (
        "1.2 million people",
        "number",
        Decimal("1200000"),
    )
    assert states("It covers 1.2 million people.", "It covers 900,000 people.") == [
        ("1.2 million people", "unshown")
    ]
    assert states("It covers 1.2 million people.", "It has 1,200,000 people.") == [
        ("1.2 million people", "shown")
    ]
    assert states("About 5 thousand visits.", "5,000 visits a year.") == [
        ("5 thousand visits", "shown")
    ]
