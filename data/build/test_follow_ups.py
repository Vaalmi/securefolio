"""Follow-ups: ten pairs for the calibration half, written without changing a document,
and added to a sealed set from its stored seed, changing nothing else in it."""

import hashlib
import json
from pathlib import Path

import pytest

from data.build import hard_pages, library, seal
from evals import score

DATA = Path(__file__).parent.parent
CALIBRATION = DATA / "evals/calibration/questions.json"
SET_BEFORE_FOLLOW_UPS = (
    "7c30ae479627aa1ba457fd46b13f2271418fdf3ad4b54a342bfedb83e7850d7e"
)


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _asked() -> list[dict]:
    return json.loads(CALIBRATION.read_text())


def test_the_calibration_half_has_ten_follow_ups_covering_each_case():
    asked = _asked()
    firsts = {q["id"]: q for q in score.first(asked)}
    follow_ups = [q for q in asked if q.get("follows")]
    assert len(follow_ups) == 10
    assert len({q["follows"] for q in follow_ups}) == 10, "one to a first question"
    for follow_up in follow_ups:
        assert follow_up["follows"] in firsts
        assert follow_up["standalone"]
        assert score.assessable(follow_up), f"{follow_up['id']} says what's owed"
    no_answer = [q for q in follow_ups if q["category"] in score.NO_ANSWER]
    assert len(no_answer) >= 2, "at least two whose answer isn't in the library"
    said = " ".join(q["question"] for q in follow_ups)
    assert "second one" in said, "the second one, as a document"
    assert "second limit" in said, "the second one, as a clause"
    assert "And in April?" in said, "a period carried over"
    assert "them" in said, "a scope carried over"


def test_the_sets_hash_is_still_its_first_questions():
    assert score.set_hash(_asked()) == SET_BEFORE_FOLLOW_UPS


def test_adding_the_follow_ups_writes_no_document(tmp_path):
    before = _hashes(DATA / "library")
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps(score.first(_asked()), indent=1))
    assert library.add_follow_ups(questions, library.CALIBRATION) == 10
    assert _hashes(DATA / "library") == before, "data/library byte for byte"
    assert json.loads(questions.read_text()) == _asked(), "as the generator wrote it"
    with pytest.raises(library.FollowUpsThere):
        library.add_follow_ups(questions, library.CALIBRATION)


def test_another_seeds_set_is_refused(tmp_path):
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps(score.first(_asked()), indent=1))
    with pytest.raises(library.NotThisSet):
        library.add_follow_ups(questions, library.CALIBRATION + 1)
    assert json.loads(questions.read_text()) == score.first(_asked())


@pytest.fixture
def sealed_before(tmp_path, monkeypatch):
    """A small sealed set as it was sealed before it had follow-ups."""
    real = hard_pages.build
    monkeypatch.setattr(
        hard_pages, "build", lambda out, seed, key, _b, _g: real(out, seed, key, 1, 1)
    )
    monkeypatch.setattr(library, "READY", True)
    target = tmp_path / "answers"
    seal.seal(target)
    questions = target / "questions.json"
    questions.write_text(json.dumps(score.first(json.loads(questions.read_text()))))
    return target


def test_sealing_adds_the_follow_ups_from_the_stored_seed_and_nothing_else(
    sealed_before,
):
    before = _hashes(sealed_before)
    firsts = json.loads((sealed_before / "questions.json").read_text())
    assert seal.add_follow_ups(sealed_before) == 10
    after = _hashes(sealed_before)
    changed = {name for name in after if after[name] != before.get(name)}
    assert changed == {"questions.json"}
    asked = json.loads((sealed_before / "questions.json").read_text())
    assert asked[: len(firsts)] == firsts, "the first questions as they were sealed"
    added = asked[len(firsts) :]
    assert [q["id"] for q in added] == [f"H f{n}" for n in range(1, 11)]
    assert {q["follows"] for q in added} <= {q["id"] for q in firsts}


def test_a_new_seal_has_its_follow_ups(tmp_path, monkeypatch):
    real = hard_pages.build
    monkeypatch.setattr(
        hard_pages, "build", lambda out, seed, key, _b, _g: real(out, seed, key, 1, 1)
    )
    monkeypatch.setattr(library, "READY", True)
    seal.seal(tmp_path / "answers")
    asked = json.loads((tmp_path / "answers/questions.json").read_text())
    assert sum(1 for q in asked if q.get("follows")) == 10


@pytest.mark.parametrize(
    "left_out",
    [("assessed_by",), ("assessed_by", "passage_facts", "answer_facts")],
)
def test_a_set_sealed_in_an_older_schema_gets_its_follow_ups_as_it_is(
    sealed_before, left_out
):
    # A set sealed before `assessed_by` (or before the facts fields) existed.
    questions = sealed_before / "questions.json"
    older = [
        {k: v for k, v in q.items() if k not in left_out}
        for q in json.loads(questions.read_text())
    ]
    questions.write_text(json.dumps(older))
    assert seal.add_follow_ups(sealed_before) == 10
    asked = json.loads(questions.read_text())
    assert asked[: len(older)] == older, "its first questions untouched"


def test_an_older_schema_still_needs_the_seeds_own_values(sealed_before):
    questions = sealed_before / "questions.json"
    found = json.loads(questions.read_text())
    found[0] = {k: v for k, v in found[0].items() if k != "assessed_by"}
    found[0]["question"] = "A question this seed never wrote?"
    questions.write_text(json.dumps(found))
    with pytest.raises(library.NotThisSet) as refused:
        seal.add_follow_ups(sealed_before)
    # The refusal is shown, and the seed is secret.
    seed = json.loads((sealed_before / "seed.json").read_text())["seed"]
    assert str(seed) not in str(refused.value)
