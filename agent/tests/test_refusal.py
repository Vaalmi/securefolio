"""The refusal, told apart however a model ends it: with its stop or without."""

import pytest

from agent import citations


@pytest.mark.parametrize(
    "text",
    [
        "I couldn't find enough evidence in this scope",
        "I couldn't find enough evidence in this scope.",
        "  I couldn't find enough evidence in this scope. \n",
        "I couldn't find enough evidence in this scope!",
        "i couldn't find enough evidence in this scope.",
    ],
)
def test_the_refusal_is_told_however_it_ends(text):
    assert citations.is_refusal(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "I couldn't find enough evidence in this scope, but the rent is $1,200.",
        "The rent is $1,200. I couldn't find enough evidence in this scope.",
    ],
)
def test_an_answer_that_says_more_isnt_the_refusal(text):
    assert not citations.is_refusal(text)
