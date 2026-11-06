"""Scores a table read against its expected cells: TEDS and exact cell match."""

from apted import APTED, Config
from rapidfuzz.distance import Levenshtein

Grid = list[list[str]]


class _Tree:
    def __init__(
        self, tag: str, text: str = "", children: list["_Tree"] | None = None
    ) -> None:
        self.tag = tag
        self.text = text
        self.children = children or []


class _CostModel(Config):
    def rename(self, node1: _Tree, node2: _Tree) -> float:  # type: ignore[override]
        if node1.tag != node2.tag:
            return 1.0
        if node1.tag != "td":
            return 0.0
        return Levenshtein.normalized_distance(node1.text, node2.text)


def _tree(grid: Grid) -> _Tree:
    rows = [
        _Tree("tr", children=[_Tree("td", cell.strip()) for cell in row])
        for row in grid
    ]
    return _Tree("table", children=rows)


def _size(node: _Tree) -> int:
    return 1 + sum(_size(child) for child in node.children)


def teds(read: Grid, expected: Grid) -> float:
    """1 is identical; 0 shares nothing, over the bigger table's node count."""
    a, b = _tree(read), _tree(expected)
    distance = APTED(a, b, _CostModel()).compute_edit_distance()
    return 1 - distance / max(_size(a), _size(b))


def exact_cell_match(read: Grid, expected: Grid) -> float:
    """The share of expected cells read exactly, compared by position."""
    total = hit = 0
    for r, row in enumerate(expected):
        for c, cell in enumerate(row):
            total += 1
            if (
                r < len(read)
                and c < len(read[r])
                and read[r][c].strip() == cell.strip()
            ):
                hit += 1
    return hit / total if total else 1.0


def markdown_to_grid(text: str) -> Grid:
    """The cells of the first Markdown table in `text`."""
    grid: Grid = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            if grid:
                break
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        grid.append(cells)
    return grid


def value_within(
    read: float, expected: float, full_scale: float, share: float = 0.02
) -> bool:
    return abs(read - expected) <= share * full_scale
