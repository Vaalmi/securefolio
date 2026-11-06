"""The table and chart scorers."""

from evals.tables import exact_cell_match, markdown_to_grid, teds, value_within


def test_identical_tables_score_one():
    grid = [["a", "b"], ["1", "2"]]
    assert teds(grid, grid) == 1.0
    assert exact_cell_match(grid, grid) == 1.0


def test_a_wrong_cell_lowers_both_scores():
    assert teds([["a", "x"]], [["a", "b"]]) < 1.0
    assert exact_cell_match([["a", "x"]], [["a", "b"]]) == 0.5


def test_a_missing_row_counts_against_exact_match():
    assert exact_cell_match([["a"]], [["a"], ["b"]]) == 0.5
    assert exact_cell_match([], []) == 1.0


def test_a_markdown_table_becomes_a_grid():
    text = "Before\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nAfter\n| c |"
    assert markdown_to_grid(text) == [["a", "b"], ["1", "2"]]


def test_a_chart_value_is_within_two_percent_of_full_scale():
    assert value_within(51, 50, 100)
    assert not value_within(53, 50, 100)
