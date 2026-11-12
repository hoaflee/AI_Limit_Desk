"""Read-only daily token reports from local Codex and Claude Code session logs.

Never reads prompts, user-message bodies, cookies, passwords, or secret stores. Only
JSONL usage fields and timestamps are used; no transcript content is retained.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _day(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _nonneg_number(obj: Any) -> int:
    if isinstance(obj, bool):
        return 0
    try:
        val = int(obj)
        return val if val >= 0 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def _total_codex(usage: dict) -> int:
    if usage.get("total_tokens") is not None:
        return _nonneg_number(usage["total_tokens"])
    # cached_input_tokens is included in input_tokens (do not add twice).
    return _nonneg_number(usage.get("input_tokens")) + _nonneg_number(usage.get("output_tokens"))


def _total_claude(usage: dict) -> int:
    # Claude's input_tokens excludes cache read/write tokens.
    cache_write = usage.get("cache_creation_input_tokens")
    if cache_write is None:
        cache = usage.get("cache_creation", {})
        cache_write = (cache.get("ephemeral_5m_input_tokens", 0) +
                       cache.get("ephemeral_1h_input_tokens", 0)) if isinstance(cache, dict) else 0
    return sum((_nonneg_number(usage.get("input_tokens")),
                _nonneg_number(usage.get("output_tokens")),
                _nonneg_number(usage.get("cache_read_input_tokens")),
                _nonneg_number(cache_write)))


def _read_rows(file: Path):
    # Maximum log line length protects against accidentally loading transcript-sized rows.
    try:
        with file.open("r", encoding="utf-8", errors="replace") as stream:
            for raw in stream:
                if len(raw) > 2_000_000:
                    continue
                try:
                    row = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def parse_codex_file(file: Path) -> dict[str, int]:
    """Use the cumulative total delta to deduplicate snapshots and resumed sessions."""
    daily: dict[str, int] = defaultdict(int)
    previous: int | None = None
    seen_fallback: set[tuple[str, int]] = set()
    for row in _read_rows(file):
        payload = row.get("payload") or {}
        if (row.get("type") != "event_msg" or not isinstance(payload, dict)
                or payload.get("type") != "token_count"):
            continue
        info = payload.get("info")
        if not isinstance(info, dict):
            continue
        key = _day(row.get("timestamp"))
        if key is None:
            continue
        cumulative = info.get("total_token_usage")
        if isinstance(cumulative, dict):
            total = _total_codex(cumulative)
            diff = total if previous is None or total < previous else total - previous
            previous = total
            if diff > 0:
                daily[key] += diff
        elif isinstance(info.get("last_token_usage"), dict):
            # Older logs may lack cumulative totals; avoid identical repeated snapshots.
            last = _total_codex(info["last_token_usage"])
            unique = (str(row.get("timestamp")), last)
            if last and unique not in seen_fallback:
                daily[key] += last
                seen_fallback.add(unique)
    return dict(daily)


def parse_claude_file(file: Path) -> dict[str, int]:
    """De-duplicate streaming assistant messages by message ID within each session."""
    records: dict[str, tuple[int, str]] = {}
    for row in _read_rows(file):
        if row.get("type") != "assistant":
            continue
        msg = row.get("message")
        if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
            continue
        day = _day(row.get("timestamp"))
        if not day:
            continue
        amount = _total_claude(msg["usage"])
        if amount == 0:
            continue
        key = str(msg.get("id") or row.get("uuid") or row.get("timestamp"))
        if key not in records or amount > records[key][0]:
            records[key] = (amount, day)
    daily: dict[str, int] = defaultdict(int)
    for amount, day in records.values():
        daily[day] += amount
    return dict(daily)


class LocalTokenHistory:
    """Caches parsed *per-file totals* until its mtime or file size changes."""

    def __init__(self, home: Path | None = None):
        self.home = home or Path.home()
        self._file_cache: dict[str, tuple[tuple[int, int], dict[str, int]]] = {}
        self.warnings: list[str] = []

    def _scan(self, root: Path, name: str, parser, cutoff: date, cap: int = 2500) -> dict[str, int]:
        output: dict[str, int] = defaultdict(int)
        if not root.exists():
            return {}
        try:
            paths = root.rglob(name)
            found = 0
            for file in paths:
                if found >= cap:
                    self.warnings.append(f"Vượt giới hạn {cap} tệp dưới {root.name}")
                    break
                try:
                    stat = file.stat()
                except OSError:
                    continue
                if stat.st_size > 150_000_000:
                    continue
                if datetime.fromtimestamp(stat.st_mtime).date() < cutoff:
                    continue
                found += 1
                fingerprint = (stat.st_mtime_ns, stat.st_size)
                pkey = str(file.resolve())
                cached = self._file_cache.get(pkey)
                if cached is None or cached[0] != fingerprint:
                    try:
                        amounts = parser(file)
                    except (OSError, UnicodeError, ValueError):
                        amounts = {}
                    self._file_cache[pkey] = (fingerprint, amounts)
                else:
                    amounts = cached[1]
                for day, tokens in amounts.items():
                    if day >= cutoff.isoformat():
                        output[day] += tokens
        except OSError:
            self.warnings.append(f"Không thể truy cập log: {root}")
        return dict(output)

    def read(self, days: int = 7, today: date | None = None) -> dict[str, dict[str, int]]:
        """Return {codex: {YYYY-MM-DD: tokens}, claude: {...}}; missing != zero."""
        self.warnings.clear()
        today = today or datetime.now().date()
        cutoff = today - timedelta(days=days - 1)
        codex_root = Path(os.getenv("CODEX_HOME", str(self.home / ".codex")))
        claude_root = Path(os.getenv("CLAUDE_CONFIG_DIR", str(self.home / ".claude")))
        codex = self._scan(codex_root / "sessions", "rollout-*.jsonl", parse_codex_file, cutoff)
        archived = self._scan(codex_root / "archived_sessions", "rollout-*.jsonl", parse_codex_file, cutoff)
        for key, n in archived.items():
            codex[key] = codex.get(key, 0) + n
        claude = self._scan(claude_root / "projects", "*.jsonl", parse_claude_file, cutoff)
        return {"codex": codex, "claude": claude}


def parse_codex_server_daily(data: Any) -> dict[str, int]:
    """Optional account/usage/read dailyUsageBuckets; dates are UTC API calendar days."""
    result: dict[str, int] = defaultdict(int)
    if not isinstance(data, dict):
        return {}
    buckets = data.get("dailyUsageBuckets")
    if not isinstance(buckets, list):
        return {}
    for row in buckets:
        if not isinstance(row, dict):
            continue
        day = row.get("startDate")
        if not isinstance(day, str):
            continue
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        count = _nonneg_number(row.get("tokens"))
        result[day] += count
    return dict(result)
