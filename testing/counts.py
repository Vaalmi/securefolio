"""Counts parses and embeds, so a test can show there were none."""

from dataclasses import dataclass


@dataclass
class Counts:
    parses: int = 0
    embeds: int = 0

    def reset(self) -> None:
        self.parses = self.embeds = 0

    def wrap_parse(self, real):
        def counted(*args):
            self.parses += 1
            return real(*args)

        return counted

    def wrap_embed(self, real):
        def counted(texts):
            self.embeds += 1
            return real(texts)

        return counted
