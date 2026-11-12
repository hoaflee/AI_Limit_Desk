"""Per-provider polling cadence, rate-limit backoff and stale-card wording.

Pure logic (no Tk, no network) so it can be unit-tested. Anthropic's OAuth usage
endpoint is undocumented and throttles aggressively: polling it at the widget's
global 30 s cadence keeps it permanently in HTTP 429, so it gets its own slower
cadence and an exponential backoff once a 429 is seen.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Callable, Optional

# Minimum seconds between automatic polls, regardless of the global interval.
MIN_INTERVALS = {'claude': 180}
BACKOFF_BASE = 300      # first pause after a 429: 5 min, then 10, 20, ...
BACKOFF_CAP = 1800      # ... never longer than 30 min unless the server asks for it
RETRY_AFTER_CAP = 3600  # honour a server-provided Retry-After up to 1 h
MANUAL_MIN_GAP = 30     # a manual refresh may not hammer a provider more often


class PollPolicy:
    """Decides which providers may be polled now. Single-threaded (Tk thread)."""

    def __init__(self, min_intervals: Optional[dict[str, float]] = None,
                 backoff_base: float = BACKOFF_BASE, backoff_cap: float = BACKOFF_CAP,
                 manual_gap: float = MANUAL_MIN_GAP):
        self.min_intervals = dict(MIN_INTERVALS if min_intervals is None else min_intervals)
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.manual_gap = manual_gap
        self._last_attempt: dict[str, datetime] = {}
        self._blocked_until: dict[str, datetime] = {}
        self._rate_limit_hits: dict[str, int] = {}

    def interval(self, ident: str, base_interval: float) -> float:
        return max(base_interval, self.min_intervals.get(ident, 0))

    def due(self, ident: str, base_interval: float, now: datetime,
            manual: bool = False) -> bool:
        """A backoff always wins; a manual refresh only skips the normal interval."""
        blocked = self._blocked_until.get(ident)
        if blocked is not None and now < blocked:
            return False
        last = self._last_attempt.get(ident)
        if last is None:
            return True
        gap = self.manual_gap if manual else self.interval(ident, base_interval)
        return (now - last).total_seconds() >= gap

    def mark_attempt(self, ident: str, now: datetime) -> None:
        self._last_attempt[ident] = now

    def record_success(self, ident: str) -> None:
        self._rate_limit_hits.pop(ident, None)
        self._blocked_until.pop(ident, None)

    def record_rate_limit(self, ident: str, now: datetime,
                          retry_after: Optional[float] = None) -> datetime:
        hits = self._rate_limit_hits.get(ident, 0) + 1
        self._rate_limit_hits[ident] = hits
        delay = min(self.backoff_cap, self.backoff_base * 2 ** (hits - 1))
        if retry_after and retry_after > 0:
            delay = max(delay, min(retry_after, RETRY_AFTER_CAP))
        until = now + timedelta(seconds=delay)
        self._blocked_until[ident] = until
        return until

    def retry_in(self, ident: str, now: datetime) -> Optional[float]:
        """Seconds until the provider may be polled again, None when not blocked."""
        blocked = self._blocked_until.get(ident)
        if blocked is None or now >= blocked:
            return None
        return (blocked - now).total_seconds()


def format_age(seconds: float) -> str:
    """'9 phút trước' for the 'CŨ' card; minute granularity so it redraws rarely."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return 'dưới 1 phút trước'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} phút trước'
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f'{hours} giờ {minutes} phút trước' if minutes else f'{hours} giờ trước'
    return f'{hours // 24} ngày trước'


def format_wait(seconds: float, compact: bool = False) -> str:
    """Round up to whole minutes so the text only changes once a minute."""
    minutes = max(1, math.ceil(seconds / 60))
    if minutes < 60:
        return f'{minutes}p' if compact else f'{minutes} phút'
    hours, minutes = divmod(minutes, 60)
    if compact:
        return f'{hours}g{minutes:02d}' if minutes else f'{hours}g'
    return f'{hours} giờ {minutes} phút' if minutes else f'{hours} giờ'


def stale_line_candidates(message: str, updated: Optional[datetime], now: datetime,
                          retry_in: Optional[float] = None) -> list[str]:
    """Text for the stale card, most informative first; the caller picks what fits."""
    head = 'Cũ'
    if updated is not None:
        head += ' ' + format_age((now - updated).total_seconds())
    reason = (message or '').strip()
    base = f'{head} · {reason}' if reason else head
    if retry_in is None:
        return [base]
    return [f'{base} · thử lại sau {format_wait(retry_in)}',
            f'{base} · thử lại {format_wait(retry_in, compact=True)}',
            base]


def fit_line(candidates: list[str], measure: Callable[[str], float], max_px: float) -> str:
    """First candidate that fits; otherwise the last one cut with an ellipsis."""
    for candidate in candidates:
        if measure(candidate) <= max_px:
            return candidate
    last = candidates[-1]
    lo, hi = 0, len(last)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(last[:mid].rstrip() + '…') <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return last[:lo].rstrip() + '…'
