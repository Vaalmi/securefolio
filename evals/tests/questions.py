"""The question the evaluation's tests ask, over the synthetic library's lease."""

from pathlib import Path

SOL = "openai:gpt-6.1-sol"
DATA = Path(__file__).parents[2] / "data" / "library"
QUESTION = {
    "id": "q1",
    "category": "fact",
    "question": "What is the pet deposit on my apartment lease?",
    "expected_document": "Home/Apartment lease.pdf",
    "expected_page": 3,
    "answer": "$299.11",
    "documents": [],
    "private": False,
    "passage_facts": {},
    "answer_facts": [],
}
# The same question worded so it waits on a plan: a first "What is" question goes
# straight to search, and the tests of a helper call need the plan.
PLANNED = {**QUESTION, "question": "Which deposits on my apartment lease are for pets?"}
