"""Pure responsive height calculations for the no-scroll Windows widget.

Every quota and the full token chart are part of the window: no scroll state.
Compact density profiles help the dashboard fit within the Windows work area.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

HEADER_HEIGHT = 54
FOOTER_HEIGHT = 35
TOP_PADDING = 10
CHART_GAP = 1


@dataclass(frozen=True)
class Layout:
    name: str
    row_height: int
    card_header: int
    card_gap: int
    empty_card: int
    chart_height: int
    bottom_padding: int

    @property
    def row_origin(self) -> int:
        return self.card_header - 6

    def card_height(self, quota_count: int, stale: bool = False) -> int:
        if quota_count == 0:
            return self.empty_card
        return self.card_header + quota_count * self.row_height + 2 + (17 if stale else 0)

    def body_height(self, cards: Iterable[tuple[int, bool]]) -> int:
        specs = list(cards)
        if specs:
            cards_h = sum(self.card_height(n, stale) + self.card_gap for n, stale in specs)
        else:
            cards_h = 101
        return TOP_PADDING + cards_h + CHART_GAP + self.chart_height + self.bottom_padding

    def window_height(self, cards: Iterable[tuple[int, bool]]) -> int:
        return HEADER_HEIGHT + FOOTER_HEIGHT + self.body_height(cards)


# First usable density wins; all preserve titles, quota labels, percentages and resets.
PROFILES = (
    Layout('comfortable', 52, 56, 11, 102, 305, 23),
    Layout('medium', 45, 52, 10, 94, 275, 20),
    Layout('compact', 38, 47, 9, 86, 245, 16),
    Layout('dense', 33, 43, 7, 80, 225, 14),
    Layout('ultra', 29, 39, 6, 73, 195, 12),
    Layout('minimum', 26, 37, 5, 67, 190, 11),
)


def fit_layout(cards: Iterable[tuple[int, bool]], available_height: int) -> tuple[Layout, int]:
    """Prefer readability, adapt to actual quota count and monitor work area.

    Extremely large custom feeds cannot physically fit into tiny screens;
    return the minimum-density natural height rather than silently clipping
    or hiding quotas. The user can disable unnecessary providers in Settings.
    """
    specs = list(cards)
    for candidate in PROFILES:
        h = candidate.window_height(specs)
        if h <= available_height:
            return candidate, h
    minimum = PROFILES[-1]
    return minimum, minimum.window_height(specs)
