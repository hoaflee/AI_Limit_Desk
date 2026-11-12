"""Pure UI preference logic. No personal data or network calls."""
from __future__ import annotations

from datetime import date, timedelta

BASE_KEYS = ('codex', 'claude', 'antigravity')
DEFAULT_VISIBLE = {'codex': True, 'claude': True, 'antigravity': False}
PRESET_DAYS = (7, 14, 30)
MAX_CHART_DAYS = 30
HISTORY_DAYS = 90


def valid_visible(raw) -> dict[str, bool]:
    """Preserve user-defined custom service flags; hide Antigravity by default."""
    result = DEFAULT_VISIBLE.copy()
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(key, str) and key and len(key) < 120 and isinstance(value, bool):
                result[key] = value
    return result


def allowed_chart_days(raw) -> int:
    try:
        n = int(raw)
    except (ValueError, TypeError, OverflowError):
        return 7
    return max(1, min(MAX_CHART_DAYS, n))


def normalize_chart_end(raw, today: date | None = None) -> str | None:
    """None means a rolling range ending today. Legacy/invalid values reset."""
    today = today or date.today()
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    if parsed >= today:
        return None
    return parsed.isoformat()


def chart_window(days: int, end_value: str | None = None,
                 today: date | None = None) -> tuple[date, date]:
    """Compute up to 30 day range inside the last 90 days, no fake future bars."""
    today = today or date.today()
    days = allowed_chart_days(days)
    try:
        end = date.fromisoformat(end_value) if end_value else today
    except (ValueError, TypeError):
        end = today
    latest_start = today - timedelta(days=HISTORY_DAYS - 1)
    earliest_end = latest_start + timedelta(days=days - 1)
    end = max(earliest_end, min(end, today))
    return end - timedelta(days=days - 1), end


def move_window(days: int, end_value: str | None, amount: int,
                today: date | None = None) -> str | None:
    today = today or date.today()
    _, end = chart_window(days, end_value, today)
    proposed = end + timedelta(days=amount * allowed_chart_days(days))
    _, fixed_end = chart_window(days, proposed.isoformat(), today)
    return None if fixed_end >= today else fixed_end.isoformat()


def choose_custom(start_s: str, end_s: str,
                  today: date | None = None) -> tuple[int, str | None]:
    today = today or date.today()
    try:
        start = date.fromisoformat(start_s.strip())
        end = date.fromisoformat(end_s.strip())
    except (ValueError, AttributeError):
        raise ValueError('Ngày không hợp lệ. Nhập theo định dạng YYYY-MM-DD.') from None
    days = (end - start).days + 1
    if days < 1 or days > MAX_CHART_DAYS:
        raise ValueError('Chọn từ 1 đến 30 ngày, ngày kết thúc không trước ngày bắt đầu.')
    if end > today:
        raise ValueError('Ngày kết thúc không được nằm trong tương lai.')
    if start < today - timedelta(days=HISTORY_DAYS - 1):
        raise ValueError(f'Hiện hỗ trợ xem lịch sử trong {HISTORY_DAYS} ngày gần nhất.')
    return days, None if end == today else end.isoformat()
