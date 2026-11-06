"""Sealing writes only under its folder, once, and never prints a held-out value."""

import pytest

from data.build import hard_pages, library, seal


@pytest.fixture
def small_set(monkeypatch):
    real = hard_pages.build

    def build(out, seed, key, _bad, _good):
        return real(out, seed, key, 1, 1)

    monkeypatch.setattr(hard_pages, "build", build)
    monkeypatch.setattr(library, "READY", True)


def test_sealing_writes_only_under_its_folder_and_only_once(tmp_path, small_set):
    target = tmp_path / "answers"
    seal.seal(target)
    outside = {p for p in tmp_path.iterdir() if p != target}
    assert outside == set()
    assert (target / "seed.json").exists()
    assert (target / "hard_pages.json").exists()
    with pytest.raises(seal.AlreadySealed):
        seal.seal(target)


def test_re_sealing_needs_asking(tmp_path, small_set):
    target = tmp_path / "answers"
    seal.seal(target)
    first = (target / "seed.json").read_text()
    seal.seal(target, reseed=True)
    assert (target / "seed.json").read_text() != first


def test_the_keys_describe_every_page(tmp_path):
    fixtures = hard_pages.build(tmp_path / "f", 5, tmp_path / "k.json", 3, 3)
    kinds = {f.kind for f in fixtures}
    assert {
        "table",
        "table_scan",
        "chart",
        "bad_text_layer",
        "good_text_layer",
    } <= kinds
    assert {"grounding", "transformed", "figure", "layout"} <= kinds
    assert sum(f.kind == "bad_text_layer" for f in fixtures) == 3
    assert all((tmp_path / "f" / f.file).exists() for f in fixtures)


def test_sealing_part_of_a_set_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "READY", False)
    with pytest.raises(seal.NotReady):
        seal.seal(tmp_path / "answers")
    assert not (tmp_path / "answers").exists()
