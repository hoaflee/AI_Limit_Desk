"""Private per-user settings/cache and Windows HKCU autostart toggle."""
from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from pathlib import Path

from providers import Snapshot
from view_state import (DEFAULT_VISIBLE, allowed_chart_days,
                        normalize_chart_end, valid_visible)

APP_DIR = Path(os.environ.get('LOCALAPPDATA', '')) / 'AI_Limit_Desk' if os.environ.get('LOCALAPPDATA') else Path.home() / '.ai_limit_desk'
CONFIG_PATH = APP_DIR / 'settings.json'
CACHE_PATH = APP_DIR / 'cache.json'
CUSTOM_PATH = APP_DIR / 'custom_usage.json'
LOGO_DIR = APP_DIR / 'logos'
DEFAULTS = {
    'interval': 30, 'always_on_top': True, 'compact': False,
    'opacity': 0.96, 'theme': 'dark',
    'visible_providers': DEFAULT_VISIBLE.copy(),
    'chart_days': 7, 'chart_end': None, 'show_chart': True,
    'x': None, 'y': None, 'autostart': False,
    'width': 440, 'height': None, 'manual_height': False,
}


def ensure_dir():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    if not CUSTOM_PATH.exists():
        CUSTOM_PATH.write_text(json.dumps({
            '_help': 'Optional additional providers. No passwords or tokens. Quotas and/or daily_tokens (7-day local dates).',
            'providers': []
        }, ensure_ascii=False, indent=2), encoding='utf-8')


def load_settings() -> dict:
    ensure_dir()
    values = deepcopy(DEFAULTS)
    try:
        obj = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        if isinstance(obj, dict):
            for key in DEFAULTS:
                if key in obj:
                    values[key] = obj[key]
    except (ValueError, OSError, TypeError):
        pass
    try:
        values['interval'] = max(15, min(600, int(values['interval'])))
    except (ValueError, TypeError):
        values['interval'] = 30
    try:
        values['opacity'] = max(0.55, min(1.0, round(float(values['opacity']), 2)))
    except (ValueError, TypeError):
        values['opacity'] = 0.96
    try:
        values['width'] = max(390, min(2500, int(values['width'])))
    except (TypeError, ValueError, OverflowError):
        values['width'] = 440
    try:
        values['height'] = max(1, min(2400, int(values['height']))) if values['height'] is not None else None
    except (TypeError, ValueError, OverflowError):
        values['height'] = None
    values['manual_height'] = values['manual_height'] is True and values['height'] is not None
    values['theme'] = values['theme'] if values['theme'] in ('dark', 'light') else 'dark'
    values['visible_providers'] = valid_visible(values['visible_providers'])
    values['chart_days'] = allowed_chart_days(values['chart_days'])
    values['chart_end'] = normalize_chart_end(values['chart_end'])
    values['show_chart'] = values['show_chart'] is not False
    if sys.platform == 'win32':
        values['autostart'] = get_autostart()
    return values


def _write_atomic(path: Path, obj: dict):
    ensure_dir()
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def save_settings(values: dict):
    _write_atomic(CONFIG_PATH, {k: values.get(k, v) for k, v in DEFAULTS.items()})


def save_cache(items: list[Snapshot]):
    _write_atomic(CACHE_PATH, {'snapshots': [s.to_dict() for s in items if s.quotas or s.daily_tokens]})


def load_cache() -> dict[str, Snapshot]:
    try:
        obj = json.loads(CACHE_PATH.read_text(encoding='utf-8'))
        return {item.id: item for item in
                (Snapshot.from_dict(x) for x in obj.get('snapshots', []) if isinstance(x, dict))}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def _startup_command() -> str:
    if getattr(sys, 'frozen', False):
        return '"' + str(Path(sys.executable).resolve()) + '"'
    exe = Path(sys.executable).resolve()
    silent = exe.with_name('pythonw.exe')
    if silent.exists():
        exe = silent
    file = Path(__file__).with_name('main.py').resolve()
    return '"' + str(exe) + '" "' + str(file) + '"'


def get_autostart() -> bool:
    if sys.platform != 'win32':
        return False
    import winreg
    path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, 'AI Limit Desk')
            return kind == winreg.REG_SZ and bool(value)
    except OSError:
        return False


def sync_autostart_command():
    """If startup was enabled in V1/V2, replace its target with this V5 install.

    Never creates a new startup entry when the user has the option disabled.
    """
    if sys.platform != 'win32':
        return
    import winreg
    path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            command, kind = winreg.QueryValueEx(key, 'AI Limit Desk')
    except OSError:
        return
    if kind != winreg.REG_SZ or not command:
        return
    if command != _startup_command() and 'AI_Limit_Desk' in command:
        set_autostart(True)


def set_autostart(enabled: bool):
    if sys.platform != 'win32':
        raise RuntimeError('Tự khởi động chỉ hoạt động trên Windows.')
    import winreg
    path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, 'AI Limit Desk', 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, 'AI Limit Desk')
            except FileNotFoundError:
                pass
