"""Pure geometry / density helpers for the borderless, mouse-resizable widget."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from layout import PROFILES, Layout, fit_layout

DEFAULT_WIDTH = 440
MIN_WIDTH = 390
EDGE_PIXELS = 11


@dataclass(frozen=True)
class WindowRect:
    x: int
    y: int
    width: int
    height: int


def hit_test_resize(x: int, y: int, width: int, height: int) -> str:
    """Named sides/corners; interior is not a resize target."""
    # A generous bottom-right corner target matches the drawn diagonal grip.
    if x >= width-26 and y >= height-26:
        return 'se'
    horiz = 'w' if x <= EDGE_PIXELS else ('e' if x >= width - EDGE_PIXELS else '')
    vert = 'n' if y <= EDGE_PIXELS else ('s' if y >= height - EDGE_PIXELS else '')
    return vert + horiz if vert else horiz


def layout_for_size(cards: Iterable[tuple[int, bool]], area_height: int,
                    requested_height: int | None = None,
                    show_chart: bool = True) -> tuple[Layout, int]:
    """Honor manual height, increasing density before permitting any clipping.

    Excess manual height grows the plot, never creates a blank empty footer.
    """
    specs = list(cards)
    if requested_height is None or not show_chart:
        # Without the chart there is nothing that could absorb extra height, so
        # the window simply hugs the quota cards.
        return fit_layout(specs, area_height, show_chart)
    requested = max(1, min(int(requested_height), area_height))
    density, minimum = fit_layout(specs, requested)
    actual = max(requested, minimum)
    extra = actual - minimum
    if extra:
        density = replace(density, chart_height=density.chart_height + extra)
    return density, actual


def minimum_height(cards: Iterable[tuple[int, bool]], show_chart: bool = True) -> int:
    return PROFILES[-1].window_height(cards, show_chart)


def resize_rect(start: WindowRect, handle: str, dx: int, dy: int,
                area: tuple[int, int, int, int], min_height: int,
                min_width: int = MIN_WIDTH) -> WindowRect:
    """Size frame by grabbing any corner/edge while respecting the work area.

    The anchor on the opposite edge stays fixed when clamping at minimum size.
    """
    bound_left, bound_top, bound_right, bound_bottom = area
    l,t,r,b = start.x,start.y,start.x+start.width,start.y+start.height
    if 'w' in handle:
        l = max(bound_left + 4, min(l + dx, r - min_width))
    elif 'e' in handle:
        r = min(bound_right - 4, max(r + dx, l + min_width))
    if 'n' in handle:
        t = max(bound_top + 4, min(t + dy, b - min_height))
    elif 's' in handle:
        b = min(bound_bottom - 4, max(b + dy, t + min_height))
    return WindowRect(int(l),int(t),int(r-l),int(b-t))
