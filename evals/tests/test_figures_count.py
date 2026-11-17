"""The figure count over a kept run: it asks no model and opens no vault, joins each
citation to the passage the model was given, writes the marked figures to a sheet, and
reads the sorted sheet back."""

import csv
import json

import pytest

from evals import cli, figures
from evals import kept as keeping
from evals.kept import Kept
from evals.tests.kept_runs import LOCAL, answer, kept_run

PDF = {"kind": "pdf", "page": 3, "rects": [], "precision": "element"}
LEASE = "Tenant may keep one cat with a pet deposit of $299.11. Rent is $1,450."
HANDED = [
    {
        "title": "Apartment lease",
        "elements": [{"id": "e1", "text": LEASE, "locator": PDF}],
    }
]


def lease_row(question: str, sentences: list[dict], follows: str | None = None) -> dict:
    return answer(
        question,
        True,
        follows=follows,
        sentences=sentences,
        citations=[
            {
                "n": 1,
                "title": "Apartment lease",
                "label": "Page 3",
                "locator": PDF,
                "quote": LEASE,
            }
        ],
        written={"handed": HANDED, "table": None},
    )


@pytest.fixture
def run(tmp_path):
    rows = [
        lease_row("q1", [{"text": "The deposit is $299.11. ", "cite": [1]}]),
        lease_row(
            "q2",
            [
                {"text": "Rent is $1,450. ", "cite": [1]},
                {"text": "Together that is $1,749.11. ", "cite": [1]},
            ],
        ),
        lease_row("f2", [{"text": "Rent is $1,450. ", "cite": [1]}], follows="q2"),
        lease_row("f1", [{"text": "It is $299.11. ", "cite": [1]}], follows="q1"),
    ]
    return kept_run(tmp_path / "runs", LOCAL, rows)


def test_the_count_marks_only_what_its_passage_doesnt_show(run):
    counted = figures.count(Kept(run))
    assert counted["by_kind"]["money"] == {"checked": 5, "marked": 1}
    assert counted["not_joined"] == 0
    (line,) = counted["lines"]
    assert (line["question"], line["figure"], line["sort"]) == ("q2", "$1,749.11", "")
    assert "Apartment lease: Tenant may keep" in line["passages"]


def test_the_follow_ups_reading_a_marked_answer_are_listed(run):
    assert figures.count(Kept(run))["follow_ups_changed"] == ["f2"]


def test_an_unfinished_run_isnt_counted(tmp_path):
    folder = kept_run(tmp_path / "runs", LOCAL, [], finish=False)
    with pytest.raises(figures.CantCount):
        figures.count(Kept(folder))


def test_a_citation_with_no_passage_given_is_counted_apart(tmp_path):
    row = lease_row("q1", [{"text": "It is $5. ", "cite": [1]}])
    row["written"]["handed"] = []
    counted = figures.count(Kept(kept_run(tmp_path / "runs", LOCAL, [row])))
    assert counted["not_joined"] == 1
    assert counted["by_kind"]["money"] == {"checked": 0, "marked": 0}, "left out"
    assert counted["lines"] == []


def test_the_sheet_is_written_and_its_sorts_read_back(run, tmp_path):
    sheet = figures.write_sheet(figures.count(Kept(run)), tmp_path)
    with sheet.open(newline="") as rows:
        lines: list[dict[str, str]] = list(csv.DictReader(rows))
    lines[0]["sort"] = "model"
    with sheet.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=figures.COLUMNS)
        writer.writeheader()
        writer.writerows(lines)
    report = figures.sorted_report(sheet)
    assert report["sorts"]["money"] == {"model": 1}
    assert (report["wrong_marks"], report["checked"], report["unsorted"]) == (0, 5, 0)
    assert report["within_target"]


def test_an_unsorted_line_is_counted_as_unsorted_never_guessed(run, tmp_path):
    sheet = figures.write_sheet(figures.count(Kept(run)), tmp_path)
    assert figures.sorted_report(sheet)["unsorted"] == 1


def test_the_command_asks_no_model_and_prints_the_count(
    run, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "REPORTS", tmp_path)
    assert cli.main(["--figures-from", str(run)]) == 0
    printed = capsys.readouterr().out
    assert "money 1 of 5" in printed
    assert "earlier answer carries a mark: f2" in printed
    sheet = tmp_path / "figures" / f"{run.name}.csv"
    assert sheet.exists()
    assert json.loads(sheet.with_suffix(".json").read_text())["run"] == run.name
    assert cli.main(["--figures-sorted", str(sheet)]) == 0
    assert '"unsorted": 1' in capsys.readouterr().out


def test_counting_a_run_again_never_wipes_its_sheet_sorted_by_hand(
    run, tmp_path, monkeypatch
):
    """An unsorted sheet may be counted again, but
    one with any sort or cause by hand is refused, and left as it was."""
    monkeypatch.setattr(cli, "REPORTS", tmp_path)
    assert cli.main(["--figures-from", str(run)]) == 0
    assert cli.main(["--figures-from", str(run)]) == 0, "nothing sorted yet"
    sheet = tmp_path / "figures" / f"{run.name}.csv"
    with sheet.open(newline="") as rows:
        lines: list[dict[str, str]] = list(csv.DictReader(rows))
    lines[0]["cause"] = "a sum of its passages' amounts"
    with sheet.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=figures.COLUMNS)
        writer.writeheader()
        writer.writerows(lines)
    before = sheet.read_text()
    with pytest.raises(SystemExit, match="sorted by hand"):
        cli.main(["--figures-from", str(run)])
    assert sheet.read_text() == before


def test_every_run_reports_its_figures_and_writes_its_sheet(tmp_path):
    kept = keeping.start(tmp_path / "runs", LOCAL, {"answer_model": LOCAL})
    kept.row(lease_row("q1", [{"text": "It is $5. ", "cite": [1]}]))
    report = {"kept": str(kept.folder), "answer_model": LOCAL}
    cli._finish(report, [{"id": "q1"}], tmp_path, whole=False)  # noqa: SLF001
    section = (Kept(kept.folder).report() or {})["figures"]
    assert section["by_kind"]["money"] == {"checked": 1, "marked": 1}
    assert section["sheet"] == str(tmp_path / "figures" / f"{kept.folder.name}.csv")


def test_a_run_kept_in_a_vault_keeps_its_sheet_there(tmp_path, monkeypatch):
    """A sheet holds the owner's text, so a run of their own set
    writes it beside the run, in their vault, never into the checkout."""
    checkout, vault = tmp_path / "checkout", tmp_path / "vault"
    monkeypatch.setattr(cli, "REPORTS", checkout)
    row = lease_row("q1", [{"text": "Together that is $9. ", "cite": [1]}])
    run = kept_run(vault / "evals" / "runs", LOCAL, [row])
    assert cli.main(["--figures-from", str(run)]) == 0
    assert (vault / "evals" / "figures" / f"{run.name}.csv").exists()
    assert not checkout.exists()
