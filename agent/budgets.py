"""The per-request token budgets."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    memories: int
    summary: int
    turns: int
    evidence: int
    output: int


STANDARD = Budget(memories=1000, summary=1500, turns=6000, evidence=12000, output=2000)
DEEP = Budget(memories=1000, summary=2000, turns=10000, evidence=40000, output=6000)
PINNED_MEMORIES = 500


def for_mode(mode: str) -> Budget:
    return DEEP if mode == "deep" else STANDARD
