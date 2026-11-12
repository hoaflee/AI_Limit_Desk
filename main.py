"""AI Limit Desk V5: auto-height or manually resized, all content visible.

Run: python main.py. No demo mode or sample usage is ever displayed.
The app only discovers already-signed-in local CLI/IDE clients.
"""
from __future__ import annotations

import argparse
import math
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox

from poll_policy import PollPolicy, fit_line, stale_line_candidates
from providers import (ProviderError, RateLimitError, Snapshot, antigravity, claude,
                       codex, dedupe_claude_quotas, load_custom, now_utc)
from storage import (APP_DIR, CUSTOM_PATH, LOGO_DIR, ensure_dir, load_cache,
                     load_settings, save_cache, save_settings, set_autostart,
                     sync_autostart_command)
from token_history import LocalTokenHistory
from logos import download_logos
from layout import HEADER_HEIGHT, FOOTER_HEIGHT
from resize import (DEFAULT_WIDTH, MIN_WIDTH, WindowRect, hit_test_resize,
                    layout_for_size, minimum_height, resize_rect)
from view_state import (BASE_KEYS, HISTORY_DAYS, PRESET_DAYS, chart_window,
                        choose_custom, move_window)

WIDTH = DEFAULT_WIDTH
HEADER_H = HEADER_HEIGHT
FOOTER_H = FOOTER_HEIGHT
MARGIN = 11
MASK = '#ff00ff'
FONT = 'Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans'
COLOR = {'codex': '#52B59A', 'claude': '#DF906E', 'antigravity': '#6B92EB'}
BASE = [
    ('codex', 'OpenAI · Codex / Work', 'Codex app-server'),
    ('claude', 'Claude Code', 'Claude OAuth'),
    ('antigravity', 'Google Antigravity', 'Antigravity IDE'),
]
URLS = {
    'codex': 'https://chatgpt.com/codex',
    'claude': 'https://claude.ai/settings/usage',
    'antigravity': 'https://antigravity.google/',
}
THEMES = {
    'dark': {
        'bg': '#101626', 'surface': '#192236', 'raised': '#222E45',
        'border': '#2B3850', 'text': '#EEF3FE', 'muted': '#9CABBF',
        'quiet': '#74849F', 'track': '#334157', 'divider': '#29354A',
        'accent': '#81ABF5', 'hover': '#253553',
    },
    'light': {
        'bg': '#F4F7FC', 'surface': '#FFFFFF', 'raised': '#E9EFF9',
        'border': '#DAE2F0', 'text': '#1A2A43', 'muted': '#637591',
        'quiet': '#8291A7', 'track': '#DFE6F0', 'divider': '#DDE5F1',
        'accent': '#3869BE', 'hover': '#E7EEFA',
    },
}


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def rounded(c: tk.Canvas, x1, y1, x2, y2, radius, fill, outline='', width=1):
    """Rounded smooth polygon; no bundled font or nonstandard widgets required."""
    r = min(radius, max(0, (x2-x1)/2), max(0, (y2-y1)/2))
    return c.create_polygon(
        x1+r, y1, x2-r, y1, x2, y1, x2, y1+r,
        x2, y2-r, x2, y2, x2-r, y2, x1+r, y2,
        x1, y2, x1, y2-r, x1, y1+r, x1, y1,
        smooth=True, splinesteps=18, fill=fill, outline=outline, width=width,
    )


def text(c, x, y, value, *, size=10, weight='normal', color=None, anchor='nw', width=None):
    kw = {'text': str(value), 'anchor': anchor,
          'font': (FONT, size, weight), 'fill': color or '#FFFFFF'}
    if width is not None:
        kw['width'] = width
    return c.create_text(x, y, **kw)


def shorten(val: str, length: int) -> str:
    return val if len(val) <= length else val[:max(0,length - 1)] + '…'


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000:
        return f'{n/1_000_000_000:.2f}B'
    if n >= 1_000_000:
        return f'{n/1_000_000:.2f}M'
    if n >= 1_000:
        return f'{n/1_000:.1f}K'
    return str(n)


def fmt_percent(n: float) -> str:
    return f'{n:.0f}%' if n == int(n) else f'{n:.1f}%'


def reset_text(value: datetime | None) -> str:
    if value is None:
        return 'Chưa có thời điểm reset'
    delta = int((value - now_utc()).total_seconds())
    if delta <= 0:
        return 'Đã tới giờ reset · chờ đồng bộ'
    d, remain = divmod(delta, 86400)
    h, remain = divmod(remain, 3600)
    m = math.ceil(remain / 60)
    if m == 60:
        h += 1
        m = 0
    if h == 24:
        d += 1
        h = 0
    interval = f'{d} ngày {h} giờ' if d else (f'{h} giờ {m} phút' if h else f'{m} phút')
    return f'Reset sau {interval}  ·  {value.astimezone().strftime("%d/%m %H:%M")}'


def quota_color(left: float) -> str:
    return '#F07788' if left <= 15 else '#E4AC5D' if left <= 35 else '#56C6A9'


def compact_reset(value: datetime | None) -> str:
    """Short reset label for small-screen rows, retaining the reset information."""
    if value is None:
        return 'Reset —'
    seconds = max(0, int((value - now_utc()).total_seconds()))
    if not seconds:
        return 'Chờ reset'
    days, remain = divmod(seconds, 86400)
    hours, remain = divmod(remain, 3600)
    minutes = math.ceil(remain/60)
    if minutes == 60:
        hours += 1
        minutes = 0
    if hours == 24:
        days += 1
        hours = 0
    if days:
        return f'{days}ng {hours}g'
    if hours:
        return f'{hours}g {minutes}p'
    return f'{minutes}p'


class LimitDesk:
    def __init__(self, screenshot: str | None = None):
        self.config = load_settings()
        if self.config.get('autostart'):
            try:
                sync_autostart_command()
            except OSError:
                pass  # Missing permissions must not prevent the UI from launching.
        self.colors = THEMES[self.config['theme']]
        self.snapshots: dict[str, Snapshot] = load_cache()
        if 'claude' in self.snapshots:
            self.snapshots['claude'].quotas = dedupe_claude_quotas(self.snapshots['claude'].quotas)
        self.local_history = LocalTokenHistory()
        self.local_daily: dict[str, dict[str,int]] = {}
        self.history_warnings: list[str] = []
        self.events: queue.Queue = queue.Queue()
        self.policy = PollPolicy()
        self.stale_signature_drawn: tuple = ()
        self.busy = False
        self.stopped = False
        self.last_poll: datetime | None = None
        self.layout = None
        self.doc_height = 0
        self.drag_origin: tuple | None = None
        self.resize_origin: tuple | None = None
        self.render_width = 0
        self.chart_hover: str | None = None
        self.chart_bars: list[tuple[int, int, str]] = []
        self.chart_hover_bounds = (0, 0)
        self.chart_regions: list[tuple[int,int,int,int,str]] = []
        self.link_regions: list[tuple[int,int,int,int,str]] = []
        self.img_cache: dict[tuple[str,int], object] = {}
        self.settings_root = None
        self.settings_canvas = None
        self.settings_hit = []
        self.opacity_drag = False
        # Legacy compact preference is ignored: V5 always shows the entire body.
        self.config['compact'] = False
        self.height = 0
        self.width = int(self.config.get("width", WIDTH))

        self.root = tk.Tk()
        self.root.withdraw()
        self.small_font = tkfont.Font(root=self.root, family=FONT, size=8)
        self.root.title('AI Limit Desk')  # taskbar/window manager only; not painted
        self.root.configure(bg=MASK)
        self.root.overrideredirect(True)
        if sys.platform == 'win32':
            try:
                self.root.wm_attributes('-transparentcolor', MASK)
            except tk.TclError:
                pass
        self.root.wm_attributes('-topmost', bool(self.config['always_on_top']))
        self.apply_opacity()
        left, top, right, bottom = self.work_area()
        self.width = max(MIN_WIDTH, min(self.width, max(MIN_WIDTH, right-left-16)))
        self.config['width'] = self.width
        cards = self.card_specs()
        requested_height = self.config.get('height') if self.config.get('manual_height') else None
        self.layout, self.height = layout_for_size(cards, max(100, bottom-top-16), requested_height)
        x = self.config.get('x')
        y = self.config.get('y')
        if not isinstance(x, int) or x < left-self.width+50 or x > right-60:
            x = max(left+12, right-self.width-35)
        if not isinstance(y, int) or y < top or y > bottom-60:
            y = top+38
        y = max(top+8, min(y, bottom-self.height-8))
        self.root.geometry(f'{self.width}x{self.height}+{x}+{y}')
        self.root.resizable(False, False)  # Borderless window uses our own grip handlers.
        self.render_width = self.width

        self.frame = tk.Canvas(self.root, width=self.width, height=self.height, bg=MASK,
                               highlightthickness=0, bd=0)
        self.frame.place(x=0, y=0)
        self.body = tk.Canvas(self.root, width=self.width-2*MARGIN,
                              height=self.height-HEADER_H-FOOTER_H,
                              bg=self.colors['bg'], highlightthickness=0, bd=0)
        self.body.place(x=MARGIN, y=HEADER_H)

        self.frame.bind('<Button-1>', self.header_click)
        self.frame.bind('<B1-Motion>', self.header_drag)
        self.frame.bind('<ButtonRelease-1>', self.header_release)
        self.frame.bind('<Motion>', self.frame_move)
        self.frame.bind('<Double-Button-1>', self.frame_double_click)
        self.body.bind('<Button-1>', self.body_click)
        self.body.bind('<Motion>', self.body_move)
        self.body.bind('<Leave>', self.body_leave)
        self.root.bind('<Control-r>', lambda _: self.refresh(manual=True))
        self.root.bind('<Control-t>', lambda _: self.toggle_theme())
        self.root.protocol('WM_DELETE_WINDOW', self.quit)
        self.root.deiconify()
        self.draw()
        self.root.after(120, self.pump)
        self.root.after(1000, self.tick)
        self.root.after(250, self.refresh)
        threading.Thread(target=self.fetch_logos, daemon=True, name='LogoDownload').start()
        if screenshot:
            self.root.after(1700, lambda: self.capture(screenshot))

    def work_area(self) -> tuple[int, int, int, int]:
        """Physical monitor work area; excludes Windows taskbar when possible."""
        if sys.platform == 'win32':
            try:
                import ctypes
                from ctypes import wintypes

                class MonitorInfo(ctypes.Structure):
                    _fields_ = [('cbSize', wintypes.DWORD),
                                ('rcMonitor', wintypes.RECT),
                                ('rcWork', wintypes.RECT),
                                ('dwFlags', wintypes.DWORD)]

                user32 = ctypes.windll.user32
                # Explicit 64-bit HWND argtypes avoid handle truncation on Win64.
                user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
                user32.MonitorFromWindow.restype = wintypes.HMONITOR
                user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR,
                                                  ctypes.POINTER(MonitorInfo)]
                user32.GetMonitorInfoW.restype = wintypes.BOOL
                monitor = user32.MonitorFromWindow(self.root.winfo_id(), 2)
                info = MonitorInfo()
                info.cbSize = ctypes.sizeof(MonitorInfo)
                if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    r = info.rcWork
                    return int(r.left), int(r.top), int(r.right), int(r.bottom)
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        return (0, 0, self.root.winfo_screenwidth(),
                max(100, self.root.winfo_screenheight()-40))

    def card_specs(self) -> list[tuple[int, bool]]:
        return [(len(s.quotas), s.status == 'stale') for s in self.get_snapshots()]

    def reflow(self) -> bool:
        """Auto-size by default, but respect the user's saved mouse resize."""
        left, top, right, bottom = self.work_area()
        cards = self.card_specs()
        height_pref = self.config.get('height') if self.config.get('manual_height') else None
        layout, height = layout_for_size(cards, max(100, bottom-top-16), height_pref)
        if layout == self.layout and height == self.height and self.render_width == self.width:
            return False
        self.layout, self.height = layout, height
        self.render_width = self.width
        x = int(self.root.winfo_x())
        y = max(top+8, min(int(self.root.winfo_y()), bottom-height-8))
        self.root.geometry(f'{self.width}x{height}+{x}+{y}')
        self.frame.configure(width=self.width, height=height)
        self.body.configure(width=self.width-2*MARGIN,
                            height=height-HEADER_H-FOOTER_H)
        if self.config.get('manual_height'):
            self.config['height'] = height
        return True

    def persist(self):
        self.config['width'] = self.width
        self.config['x'] = int(self.root.winfo_x())
        self.config['y'] = int(self.root.winfo_y())
        try:
            save_settings(self.config)
        except OSError:
            pass

    def apply_opacity(self):
        try:
            self.root.wm_attributes('-alpha', self.config['opacity'])
        except tk.TclError:
            pass

    def set_opacity(self, value):
        self.config['opacity'] = round(clamp(value, 0.55, 1.0), 2)
        self.apply_opacity()
        self.persist()
        self.draw_settings()

    def set_interval(self, n):
        self.config['interval'] = n
        self.persist()
        self.draw()
        self.draw_settings()

    def is_visible(self, ident: str) -> bool:
        # Custom feeds that predate V3 remain visible unless explicitly disabled.
        return self.config['visible_providers'].get(ident, True)

    def toggle_provider(self, ident: str):
        self.config['visible_providers'][ident] = not self.is_visible(ident)
        self.chart_hover = None
        self.persist()
        self.draw()
        self.draw_settings()
        if self.is_visible(ident):
            threading.Thread(target=self.fetch_logos,daemon=True,name='LogoDownload').start()
            self.refresh(manual=True)

    def set_chart_days(self, days: int):
        self.config['chart_days'] = days
        self.chart_hover = None
        self.persist()
        self.draw_body()

    def cycle_chart_days(self):
        value = self.config['chart_days']
        new_days = PRESET_DAYS[(PRESET_DAYS.index(value)+1) % len(PRESET_DAYS)] if value in PRESET_DAYS else 7
        self.set_chart_days(new_days)

    def shift_chart(self, direction: int):
        old = self.config['chart_end']
        self.config['chart_end'] = move_window(self.config['chart_days'], old, direction)
        if old != self.config['chart_end']:
            self.chart_hover = None
            self.persist()
            self.draw_body()

    def open_date_picker(self):
        """Allow an exact 1-30-day date range without a third-party calendar library."""
        start, end = chart_window(self.config['chart_days'], self.config['chart_end'])
        window = tk.Toplevel(self.root)
        window.title('Chọn khoảng ngày · token')
        window.configure(bg=self.colors['surface'])
        window.wm_attributes('-topmost', True)
        window.resizable(False, False)
        window.geometry(f'340x208+{self.root.winfo_x()+40}+{self.root.winfo_y()+85}')
        fg, bg = self.colors['text'], self.colors['surface']
        tk.Label(window, text='Khoảng ngày muốn xem (YYYY-MM-DD)', font=(FONT, 10, 'bold'),
                 fg=fg, bg=bg).pack(pady=(13, 10))
        fields = tk.Frame(window, bg=bg)
        fields.pack()
        start_var = tk.StringVar(value=start.isoformat())
        end_var = tk.StringVar(value=end.isoformat())
        for label_, var in [('Từ', start_var), ('Đến', end_var)]:
            col = tk.Frame(fields, bg=bg)
            col.pack(side='left', padx=8)
            tk.Label(col, text=label_, font=(FONT, 9), fg=fg, bg=bg).pack(anchor='w')
            tk.Entry(col, width=15, textvariable=var, font=(FONT, 10),
                     bg=self.colors['raised'], fg=fg, insertbackground=fg,
                     relief='flat').pack(ipady=7)

        def submit():
            try:
                days, end_value = choose_custom(start_var.get(), end_var.get())
            except ValueError as exc:
                messagebox.showwarning('Khoảng ngày không hợp lệ', str(exc), parent=window)
                return
            self.config['chart_days'] = days
            self.config['chart_end'] = end_value
            self.chart_hover = None
            self.persist()
            self.draw_body()
            window.destroy()

        tk.Button(window, text='Áp dụng', command=submit, bg=self.colors['accent'], fg='white',
                  font=(FONT, 10, 'bold'), bd=0, cursor='hand2').pack(pady=13, ipadx=35, ipady=5)
        window.bind('<Return>', lambda _: submit())

    def toggle_theme(self):
        self.config['theme'] = 'light' if self.config['theme'] == 'dark' else 'dark'
        self.colors = THEMES[self.config['theme']]
        self.body.configure(bg=self.colors['bg'])
        self.persist()
        self.draw()
        self.draw_settings()

    def toggle_top(self):
        self.config['always_on_top'] = not self.config['always_on_top']
        self.root.wm_attributes('-topmost', self.config['always_on_top'])
        self.persist()
        self.draw_settings()

    def toggle_autostart(self):
        value = not self.config['autostart']
        try:
            set_autostart(value)
            self.config['autostart'] = value
            self.persist()
            self.draw_settings()
        except (OSError, RuntimeError) as exc:
            messagebox.showwarning('Windows autostart', str(exc), parent=self.settings_root or self.root)

    def quit(self):
        self.stopped = True
        self.persist()
        if self.settings_root and self.settings_root.winfo_exists():
            self.settings_root.destroy()
        self.root.destroy()

    def open_path(self, path: Path):
        try:
            if sys.platform == 'win32':
                os.startfile(str(path))
            else:
                import subprocess
                subprocess.Popen(['xdg-open', str(path)])
        except OSError:
            messagebox.showinfo('Đường dẫn', str(path), parent=self.root)

    def fetch_logos(self):
        installed = download_logos(LOGO_DIR,{key for key in BASE_KEYS if self.is_visible(key)})
        self.events.put(('logos', installed))

    def logo_path(self, key: str) -> Path:
        if key == 'codex':
            # Ships in both Light/Dark variants; legacy chatgpt favicon cache ignored.
            resource_dir = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
            return resource_dir / 'assets' / f'openai-blossom-{self.config["theme"]}.png'
        return LOGO_DIR / f'{key}.png'

    def icon(self, c: tk.Canvas, key: str, x: int, y: int, size: int = 25):
        if key in COLOR:
            path = self.logo_path(key)
            if path.is_file():
                cachekey = (key,size,self.config['theme'])
                if cachekey not in self.img_cache:
                    try:
                        from PIL import Image, ImageTk
                        with Image.open(path) as original:
                            im = original.convert('RGBA')
                            im.thumbnail((size,size), Image.Resampling.LANCZOS)
                            self.img_cache[cachekey] = ImageTk.PhotoImage(im, master=self.root)
                    except (OSError, ImportError, tk.TclError):
                        pass
                if cachekey in self.img_cache:
                    c.create_image(x,y,image=self.img_cache[cachekey])
                    return
        tint = COLOR.get(key, '#8E9BAF')
        rounded(c,x-size/2,y-size/2,x+size/2,y+size/2, size/3, tint)
        text(c,x,y, {'codex':'O','claude':'C','antigravity':'A'}.get(key,'•'),
             size=max(9,int(size*0.55)), weight='bold',color='white',anchor='center')

    def get_snapshots(self) -> list[Snapshot]:
        items = []
        for key,name,source in BASE:
            if not self.is_visible(key):
                continue
            items.append(self.snapshots.get(key) or Snapshot(key,name,source, status='error',
                    message='Đang tìm tài khoản đã đăng nhập trên máy…'))
        items.extend(s for k,s in sorted(self.snapshots.items())
                     if k.startswith('custom_') and self.is_visible(k))
        return items

    def refresh(self, manual: bool = False):
        """Poll every provider that is due; a manual refresh skips the normal interval.

        Providers in rate-limit backoff are never polled, even manually, and are
        keeping their last snapshot (shown as stale).
        """
        if self.stopped or self.busy:
            return
        now = now_utc()
        enabled = [(key, name, src, fn) for (key, name, src), fn in
                   zip(BASE, (codex, claude, antigravity))
                   if self.is_visible(key)
                   and self.policy.due(key, self.config['interval'], now, manual)]
        for key, *_ in enabled:
            self.policy.mark_attempt(key, now)
        self.busy = True
        self.last_poll = now
        self.draw_frame()
        def job():
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix='QuotaProvider') as pool:
                futures = [(key, name, src, pool.submit(fn)) for key, name, src, fn in enabled]
                history_future = pool.submit(self.local_history.read, HISTORY_DAYS)
                for ident,title_,source, future in futures:
                    try:
                        item = future.result(timeout=23)
                    except RateLimitError as exc:
                        self.events.put(('rate_limit', (ident, exc.retry_after)))
                        item = Snapshot(ident, title_, source, status='error', message=str(exc))
                    except ProviderError as exc:
                        item = Snapshot(ident, title_, source, status='error', message=str(exc))
                    except Exception:
                        item = Snapshot(ident,title_,source,status='error',
                                        message='Không đọc được trạng thái. Kiểm tra ứng dụng và phiên đăng nhập.')
                    self.events.put(('quota', item))
                try:
                    custom = load_custom(CUSTOM_PATH)
                    self.events.put(('custom', custom))
                except ProviderError:
                    self.events.put(('custom', []))
                try:
                    self.events.put(('history', (history_future.result(timeout=16),
                                                 list(self.local_history.warnings))))
                except Exception:
                    self.events.put(('history_error', None))
            self.events.put(('finished', None))
        threading.Thread(target=job,daemon=True,name='RefreshCoordinator').start()

    def pump(self):
        if self.stopped:
            return
        changed = False
        while True:
            try:
                kind,data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'rate_limit':
                ident, retry_after = data
                self.policy.record_rate_limit(ident, now_utc(), retry_after)
            elif kind == 'quota':
                if data.status == 'ok':
                    self.policy.record_success(data.id)
                    self.snapshots[data.id] = data
                else:
                    old = self.snapshots.get(data.id)
                    if old is not None and (old.quotas or old.daily_tokens):
                        old.status = 'stale'
                        old.message = data.message
                    else:
                        self.snapshots[data.id] = data
                changed = True
            elif kind == 'custom':
                self.snapshots = {k:v for k,v in self.snapshots.items() if not k.startswith('custom_')}
                for item in data:
                    self.snapshots[item.id] = item
                changed = True
            elif kind == 'history':
                self.local_daily, self.history_warnings = data
                changed = True
            elif kind == 'logos':
                self.img_cache.clear()
                changed = True
            elif kind == 'finished':
                self.busy = False
                changed = True
                try:
                    save_cache(list(self.snapshots.values()))
                except OSError:
                    pass
        if changed:
            self.draw()
        self.root.after(130,self.pump)

    def tick(self):
        if self.stopped:
            return
        if self.last_poll and not self.busy and (now_utc()-self.last_poll).total_seconds() >= self.config['interval']:
            self.refresh()
        else:
            self.draw_frame()
            # Age / retry countdown on stale cards only changes once a minute.
            if self.stale_signature() != self.stale_signature_drawn:
                self.draw_body()
        self.root.after(1000,self.tick)

    def stale_candidates(self, s: Snapshot, now: datetime | None = None) -> list[str]:
        """Wording for a stale card: how old the data is, why it failed, when we retry."""
        now = now or now_utc()
        return stale_line_candidates(s.message, s.updated, now,
                                     self.policy.retry_in(s.id, now))

    def stale_signature(self) -> tuple:
        now = now_utc()
        return tuple((s.id, tuple(self.stale_candidates(s, now)))
                     for s in self.get_snapshots() if s.status == 'stale')

    def draw(self):
        self.reflow()
        self.draw_frame()
        self.draw_body()

    def draw_frame(self):
        c = self.frame
        p = self.colors
        c.delete('all')
        rounded(c,2,2,self.width-2,self.height-2,24,p['bg'],p['border'],2)
        text(c,23,17,'HẠN MỨC  +  TOKEN',size=10,weight='bold',color=p['text'])
        tools = [(self.width-123,'↻'),(self.width-85,'☼' if self.config['theme']=='dark' else '☾'),
                 (self.width-47,'⚙'), (self.width-15,'×')]
        for x,symbol in tools:
            rounded(c,x-14,13,x+12,40,8,p['raised'])
            text(c,x-1,26,symbol,size=14,weight='bold',color=p['text'],anchor='center')
        c.create_line(22,self.height-FOOTER_H,self.width-22,self.height-FOOTER_H,
                      fill=p['divider'],width=1)
        selected = self.get_snapshots()
        connected = sum(s.status == 'ok' for s in selected)
        status = ('Đang đồng bộ…' if self.busy else
                  f'{connected}/{len(selected)} nguồn trực tuyến' if selected else 'Chưa chọn dịch vụ')
        dot = '#E6B865' if self.busy else ('#57C9A9' if selected and connected == len(selected) else p['quiet'])
        c.create_oval(23,self.height-24,31,self.height-16,fill=dot,outline='')
        text(c,39,self.height-27,status,size=9,color=p['muted'])
        age = '--:--' if not self.last_poll else self.last_poll.astimezone().strftime('%H:%M:%S')
        text(c,self.width-33,self.height-27,f'{age}   ·   {self.config["interval"]}s',
             size=9,color=p['quiet'],anchor='ne')
        # Visible corner grip makes resizing discoverable on a borderless window.
        for delta in (0, 6, 12):
            c.create_line(self.width-10-delta,self.height-8,
                          self.width-8,self.height-10-delta,
                          fill=p['quiet'],width=1)

    def body_click(self, event):
        for x1,y1,x2,y2,action in self.chart_regions:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                if action == 'prev':
                    self.shift_chart(-1)
                elif action == 'next':
                    self.shift_chart(1)
                elif action == 'days':
                    self.cycle_chart_days()
                elif action == 'date':
                    self.open_date_picker()
                return
        for x1,y1,x2,y2,ident in self.link_regions:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                if ident in URLS:
                    webbrowser.open(URLS[ident])
                break

    def body_move(self,event):
        nextday = None
        for x1,x2,day in self.chart_bars:
            if x1-7 <= event.x <= x2+7 and self.chart_hover_bounds[0] <= event.y <= self.chart_hover_bounds[1]:
                nextday = day
                break
        if nextday != self.chart_hover:
            self.chart_hover = nextday
            self.draw_body()

    def body_leave(self,event):
        if self.chart_hover:
            self.chart_hover = None
            self.draw_body()

    def draw_body(self):
        if self.reflow():
            self.draw_frame()
        c = self.body
        c.delete('all')
        self.link_regions = []
        self.chart_regions = []
        self.chart_bars = []
        y_abs = self.draw_quotas()
        self.draw_tokens(y_abs)
        self.stale_signature_drawn = self.stale_signature()
        self.doc_height = self.layout.body_height(
            (len(s.quotas), s.status == 'stale') for s in self.get_snapshots())

    def draw_quotas(self):
        c, p = self.body, self.colors
        sw = self.width - 2*MARGIN
        ly = self.layout
        y_abs = 10
        selected = self.get_snapshots()
        if not selected:
            rounded(c,11,y_abs,sw-11,y_abs+90,17,p['surface'],p['border'])
            text(c,24,y_abs+17,'Chưa có dịch vụ được bật',size=11,
                 weight='bold',color=p['text'])
            text(c,24,y_abs+48,'Nhấn ⚙ để bật Codex, Claude Code hoặc Antigravity.',
                 size=9,color=p['muted'])
            return y_abs+101
        for s in selected:
            count = len(s.quotas)
            h = ly.card_height(count,s.status=='stale')
            top = y_abs
            rounded(c,11,top,sw-11,top+h,17,p['surface'],p['border'])
            self.icon(c,s.id,37,int(top+ly.card_header/2),
                      25 if ly.row_height <= 33 else 29)
            text(c,58,top+max(9,int(ly.card_header*.27)),
                 shorten(s.title,26),size=11,weight='bold',color=p['text'])
            status={'ok':'LIVE','stale':'CŨ','error':'CHƯA KẾT NỐI'}.get(s.status,'--')
            statcolor=('#56C7A7' if s.status=='ok' else
                       '#E2AB59' if s.status=='stale' else p['quiet'])
            text(c,sw-30,top+max(12,int(ly.card_header*.34)),status,
                 size=8,weight='bold',color=statcolor,anchor='ne')
            self.link_regions.append((20,int(top+6),sw-14,
                                      int(top+ly.card_header-5),s.id))
            if count:
                for index,q in enumerate(s.quotas):
                    y = top + ly.row_origin + index*ly.row_height
                    if ly.row_height < 33:
                        # On small monitors a one-line quota + reset fits neatly
                        # above the track; multi-line content would overlap.
                        text(c,27,y,shorten(q.label,22),size=9,color=p['text'])
                        text(c,sw-104,y+1,compact_reset(q.reset_at),
                             size=7,color=p['muted'],anchor='ne')
                        text(c,sw-28,y,fmt_percent(q.left),size=9,
                             weight='bold',color=quota_color(q.left),anchor='ne')
                        bar_top = y+18
                    else:
                        text(c,27,y,shorten(q.label,38),size=10,color=p['text'])
                        text(c,sw-28,y,fmt_percent(q.left),size=10,
                             weight='bold',color=quota_color(q.left),anchor='ne')
                        bar_top = y+max(11,round(ly.row_height*.38))
                        reset_y = y+max(19,round(ly.row_height*.63))
                        text(c,27,reset_y,shorten(reset_text(q.reset_at),55),
                             size=8,color=p['muted'])
                    rounded(c,27,bar_top,sw-27,bar_top+6,3,p['track'])
                    if q.left > 0:
                        rounded(c,27,bar_top,27+(sw-54)*q.left/100,bar_top+6,
                                3,quota_color(q.left))
                if s.status=='stale':
                    line = fit_line(self.stale_candidates(s), self.small_font.measure, sw-54)
                    text(c,27,top+h-19,line,size=8,color=statcolor)
            else:
                text(c,27,top+min(55,h-36),
                     shorten(s.message or 'Chưa nhận được số liệu hạn mức',66),
                     size=9,color=p['muted'],width=sw-54)
            if s.status=='ok':
                c.create_oval(sw-75,top+18,sw-68,top+25,fill='#56C7A7',outline='')
            y_abs += h + ly.card_gap
        return y_abs + 1

    def chart_series(self):
        """A hidden provider never contributes tokens to the displayed total."""
        output = []
        codex_data = self.snapshots.get('codex')
        online_codex = (codex_data and codex_data.daily_tokens
                        and codex_data.status == 'ok')
        if self.is_visible('codex'):
            data_codex = codex_data.daily_tokens if online_codex else self.local_daily.get('codex', {})
            if data_codex:
                output.append(('codex', 'Codex', COLOR['codex'], data_codex,
                               'OpenAI UTC' if online_codex else 'Codex log cục bộ'))
        if self.is_visible('claude'):
            data_claude = self.local_daily.get('claude', {})
            if data_claude:
                output.append(('claude', 'Claude Code', COLOR['claude'], data_claude,
                               'Claude Code log cục bộ'))
        for s in self.get_snapshots():
            if s.id.startswith('custom_') and s.daily_tokens:
                output.append((s.id, shorten(s.title, 18), '#967DE0',
                               s.daily_tokens, 'Nguồn JSON'))
        return output

    def draw_tokens(self, y_abs: int):
        """Full token chart immediately follows quota cards; no scrolling."""
        c, p = self.body, self.colors
        sw = self.width - 2*MARGIN
        top = y_abs
        chart_h = self.layout.chart_height
        delta = 13 if chart_h <= 245 else (5 if chart_h <= 275 else 0)
        bottom = top+chart_h
        rounded(c,11,top,sw-11,bottom,18,p['surface'],p['border'])

        start, end = chart_window(self.config['chart_days'], self.config['chart_end'])
        days = [(start+timedelta(days=i)).isoformat()
                for i in range((end-start).days+1)]
        sources = self.chart_series()
        daily = {d: sum(max(0, int(values.get(d, 0))) for _,_,_,values,_ in sources)
                 for d in days}
        total = sum(daily.values())
        text(c,27,top+16,'TOKEN THEO NGÀY',size=10,weight='bold',color=p['text'])
        text(c,sw-27,top+11,fmt_tokens(total) if sources else '—',size=17,weight='bold',
             color=p['text'],anchor='ne')
        # if chart_h >= 225:
        #     text(c,sw-27,top+35,'tổng trong khoảng',size=8,
        #          color=p['quiet'],anchor='ne')

        def control(x1, x2, name, label_, *, active=True, weight='bold'):
            fill = p['raised'] if active else p['bg']
            color = p['text'] if active else p['quiet']
            rounded(c,x1,top+51-delta,x2,top+80-delta,9,fill)
            text(c,(x1+x2)/2,top+65-delta,label_,size=9,weight=weight,color=color,anchor='center')
            self.chart_regions.append((x1,int(top+51-delta),x2,int(top+80-delta),name))

        oldest_allowed = date.today()-timedelta(days=HISTORY_DAYS-1)
        control(26,54,'prev','‹',active=(start > oldest_allowed))
        range_label = f'{start:%d/%m} – {end:%d/%m/%y}'
        date_right = sw - 160
        control(60,date_right,'date',f'{range_label}  ▾',weight='normal')
        control(date_right+6,date_right+34,'next','›',active=(end < date.today()))
        control(date_right+40,sw-25,'days',f'{len(days)} NGÀY ▾')

        if sources:
            leg_y = top+96-delta
            if len(sources) <= 3:
                leg_dx = 122 if len(sources)==3 else 174
                for i,(_,name,color,_,_) in enumerate(sources):
                    x=28+i*leg_dx
                    c.create_oval(x,leg_y+2,x+9,leg_y+11,fill=color,outline='')
                    text(c,x+15,leg_y,shorten(name,16),size=9,color=p['text'])
            else:
                for i,(_,name,color,_,_) in enumerate(sources[:2]):
                    x=28+i*145
                    c.create_oval(x,leg_y+2,x+9,leg_y+11,fill=color,outline='')
                    text(c,x+15,leg_y,shorten(name,15),size=9,color=p['text'])
                text(c,sw-27,leg_y,f'+{len(sources)-2} nguồn',size=8,
                     color=p['muted'],anchor='ne')
        else:
            text(c,28,top+96-delta,'Chưa có dữ liệu token thực từ dịch vụ đang hiện.',
                 size=9,color=p['muted'])
            empty_top = top+128-delta
            empty_bottom = top+chart_h-43
            rounded(c,27,empty_top,sw-27,empty_bottom,12,p['raised'])
            text(c,sw/2,empty_top+max(15,(empty_bottom-empty_top)*.29),
                 'Chưa có lịch sử token',size=11,weight='bold',
                 color=p['text'],anchor='center')
            if empty_bottom-empty_top >= 76:
                text(c,sw/2,empty_top+53,'Dữ liệu sẽ xuất hiện khi CLI/IDE tạo log',
                     size=9,color=p['muted'],anchor='center')
            text(c,27,top+chart_h-27,
                 'Không có số liệu: tổng token chưa thể xác định.',
                 size=8,color=p['quiet'])
            self.chart_hover_bounds=(0,0)
            return

        graph_top = top+132-2*delta
        graph_bottom = top+chart_h-(57 if chart_h <= 275 else 69)
        graph_h = graph_bottom-graph_top
        self.chart_hover_bounds = (int(graph_top),int(graph_bottom+20))
        upper = max(1,max(daily.values(),default=0))
        chart_left, chart_right = 64, sw-30
        width = chart_right-chart_left
        for i in range(3):
            y=graph_bottom-i*graph_h/2
            c.create_line(chart_left,y,chart_right,y,fill=p['divider'],dash=(2,4))
            text(c,55,y,fmt_tokens(int(upper*i/2)),size=8,color=p['quiet'],anchor='e')

        n = max(1, len(days))
        dx = width/n
        barwidth = min(27,max(4,dx*0.59))
        label_every = 1 if n <= 7 else 2 if n <= 14 else 5
        for index,day in enumerate(days):
            x=chart_left+(index+0.5)*dx
            self.chart_bars.append((int(x-dx/2),int(x+dx/2),day))
            y_bottom = graph_bottom
            for _,_,fill,points,_ in sources:
                amount = max(0,int(points.get(day,0)))
                if amount <= 0:
                    continue
                bar_h = amount/upper*graph_h
                y_top = max(graph_top,y_bottom-max(1,bar_h))
                if y_bottom > y_top:
                    rounded(c,x-barwidth/2,y_top,x+barwidth/2,y_bottom,
                            min(4,barwidth/3),fill)
                y_bottom = y_top
            if index % label_every == 0 or index == n-1:
                label_date = datetime.strptime(day,'%Y-%m-%d')
                label = label_date.strftime('%d/%m') if n <= 14 else label_date.strftime('%d')
                text(c,x,graph_bottom+14,label,size=8,color=p['muted'],anchor='center')

        # Always clearly distinguish missing usage from a measured value of zero.
        footnote = ('Chỉ tổng hợp các nguồn có lịch sử token · không quy đổi % quota.'
                    if sources else 'Không có số liệu: tổng token chưa thể xác định.')
        text(c,27,top+chart_h-27,shorten(footnote,66),size=8,color=p['quiet'])
        if sources and any('UTC' in desc for *_,desc in sources):
            text(c,27,top+chart_h-14,'Codex API dùng ngày UTC; log dùng giờ máy.',
                 size=8,color=p['quiet'])

        if self.chart_hover in daily and sources:
            selected = self.chart_hover
            ix=days.index(selected)
            barx = chart_left+(ix+0.5)*dx
            x=clamp(barx-93,24,sw-222)
            tooltip_height = 35 + min(4,len(sources))*19 + (15 if len(sources)>4 else 0)
            y=top+108-delta
            rounded(c,x,y,x+208,y+tooltip_height,11,p['bg'],p['border'],2)
            text(c,x+11,y+9,datetime.fromisoformat(selected).strftime('%d/%m/%Y'),
                 size=10,weight='bold',color=p['text'])
            text(c,x+198,y+9,fmt_tokens(daily[selected]),size=9,weight='bold',
                 color=p['text'],anchor='ne')
            for j,(_,name,color,points,_) in enumerate(sources[:4]):
                yy=y+32+j*19
                c.create_oval(x+11,yy,x+18,yy+7,fill=color,outline='')
                text(c,x+23,yy-4,shorten(name,17),size=8,color=p['muted'])
                text(c,x+197,yy-4,fmt_tokens(max(0,int(points.get(selected,0)))),
                     size=8,color=p['text'],anchor='ne')
            if len(sources)>4:
                text(c,x+11,y+32+4*19,f'+{len(sources)-4} nguồn khác',
                     size=8,color=p['quiet'])

    def frame_move(self, event):
        if self.resize_origin is not None:
            return
        handle = hit_test_resize(event.x,event.y,self.width,self.height)
        cursor_map = {'w':'sb_h_double_arrow','e':'sb_h_double_arrow',
                      'n':'sb_v_double_arrow','s':'sb_v_double_arrow',
                      'nw':'top_left_corner','se':'bottom_right_corner',
                      'ne':'top_right_corner','sw':'bottom_left_corner'}
        try:
            self.frame.configure(cursor=cursor_map.get(handle, 'arrow'))
        except tk.TclError:
            self.frame.configure(cursor='arrow')

    def frame_double_click(self,event):
        if event.x >= self.width-29 and event.y >= self.height-29:
            self.config['manual_height'] = False
            self.config['height'] = None
            self.width = WIDTH
            self.config['width'] = WIDTH
            self.resize_origin = None
            self.draw()
            self.persist()

    def header_click(self,event):
        handle = hit_test_resize(event.x,event.y,self.width,self.height)
        if handle:
            current = WindowRect(self.root.winfo_x(),self.root.winfo_y(),
                                 self.width,self.height)
            self.resize_origin = (handle,event.x_root,event.y_root,current)
            self.drag_origin = None
            return
        if event.y >= self.height-FOOTER_H:
            if event.x < 220:
                self.refresh(manual=True)
            else:
                self.show_settings()
            return
        if event.y > HEADER_H:
            return
        if self.width-140 <= event.x < self.width-107:
            self.refresh(manual=True)
        elif self.width-102 <= event.x < self.width-68:
            self.toggle_theme()
        elif self.width-63 <= event.x < self.width-30:
            self.show_settings()
        elif event.x >= self.width-29:
            self.quit()
        else:
            self.drag_origin=(event.x_root,event.y_root,self.root.winfo_x(),self.root.winfo_y())

    def header_drag(self,event):
        if self.resize_origin:
            handle, ox, oy, original = self.resize_origin
            r = resize_rect(original,handle,event.x_root-ox,event.y_root-oy,
                            self.work_area(),minimum_height(self.card_specs()))
            if 's' in handle or 'n' in handle:
                self.config['manual_height'] = True
                self.config['height'] = r.height
            self.width = r.width
            self.root.geometry(f'{r.width}x{r.height}+{r.x}+{r.y}')
            self.draw()
            return
        if self.drag_origin:
            ex,ey,x,y = self.drag_origin
            self.root.geometry(f'+{x+event.x_root-ex}+{y+event.y_root-ey}')

    def header_release(self,event):
        if self.drag_origin or self.resize_origin:
            self.drag_origin=None
            self.resize_origin=None
            self.persist()

    def show_settings(self):
        if self.settings_root is not None and self.settings_root.winfo_exists():
            self.settings_root.lift()
            return
        p = tk.Toplevel(self.root)
        p.withdraw()
        p.title('Tùy chỉnh')
        p.configure(bg=MASK)
        p.overrideredirect(True)
        if sys.platform=='win32':
            try:
                p.wm_attributes('-transparentcolor',MASK)
            except tk.TclError:
                pass
        p.wm_attributes('-topmost',True)
        x0,y0 = self.root.winfo_x(),self.root.winfo_y()
        w,h=354,580
        x=x0-w-13 if x0>=w+20 else x0+self.width+12
        screenw=self.root.winfo_screenwidth()
        if x+w>screenw:
            x=max(0,screenw-w-10)
        y=max(0,min(y0,self.root.winfo_screenheight()-h-15))
        p.geometry(f'{w}x{h}+{x}+{y}')
        p.bind('<Escape>',lambda _: self.hide_settings())
        c=tk.Canvas(p,width=w,height=h,bg=MASK,bd=0,highlightthickness=0)
        c.place(x=0,y=0)
        self.settings_root=p
        self.settings_canvas=c
        c.bind('<Button-1>',self.settings_click)
        c.bind('<B1-Motion>',self.settings_drag)
        c.bind('<ButtonRelease-1>',lambda e: setattr(self,'opacity_drag',False))
        p.deiconify()
        self.draw_settings()

    def hide_settings(self):
        if self.settings_root and self.settings_root.winfo_exists():
            self.settings_root.destroy()
        self.settings_root=None
        self.settings_canvas=None

    def draw_settings(self):
        if self.settings_canvas is None or not self.settings_canvas.winfo_exists():
            return
        c,p=self.settings_canvas,self.colors
        c.delete('all')
        self.settings_hit=[]
        rounded(c,2,2,352,578,22,p['bg'],p['border'],2)
        text(c,23,20,'Tùy chỉnh widget',size=13,weight='bold',color=p['text'])
        text(c,331,17,'×',size=18,weight='bold',color=p['muted'],anchor='ne')
        self.settings_hit.append(((309,7,350,50),'close',None))
        c.create_line(22,58,330,58,fill=p['divider'])
        text(c,25,72,'DỊCH VỤ HIỂN THỊ',size=9,weight='bold',color=p['quiet'])
        for i,(ident,label_) in enumerate((('codex','OpenAI Codex'),
                                            ('claude','Claude Code'),
                                            ('antigravity','Google Antigravity'))):
            y=97+i*38
            if i:
                c.create_line(24,y-7,330,y-7,fill=p['divider'])
            self.icon(c,ident,38,y+10,23)
            text(c,58,y+1,label_,size=10,weight='bold',color=p['text'])
            enabled = self.is_visible(ident)
            l,t=285,y
            rounded(c,l,t,l+44,t+24,12,p['accent'] if enabled else p['track'])
            knob=l+32 if enabled else l+12
            c.create_oval(knob-8,t+4,knob+8,t+20,fill='white',outline='')
            self.settings_hit.append(((18,y-3,334,y+31),'provider',ident))
        text(c,25,219,'Ẩn khỏi widget và tổng token; không đăng xuất.',
             size=8,color=p['quiet'])
        c.create_line(22,241,330,241,fill=p['divider'])

        text(c,25,253,'Độ trong suốt',size=10,color=p['text'])
        text(c,329,253,f'{int(self.config["opacity"]*100)}%',size=10,
             weight='bold',color=p['accent'],anchor='ne')
        rounded(c,26,290,329,297,3,p['track'])
        pct=(self.config['opacity']-0.55)/0.45
        rounded(c,26,290,26+303*pct,297,3,p['accent'])
        xx=26+303*pct
        c.create_oval(xx-8,286,xx+8,302,fill=p['text'],outline=p['accent'],width=2)
        self.settings_hit.append(((19,279,335,307),'opacity',None))

        text(c,25,319,'Tự làm mới',size=10,color=p['text'])
        values=[30,60,90,120,300]
        for ix,n in enumerate(values):
            x=25+ix*63
            selected=self.config['interval']==n
            rounded(c,x,341,x+57,371,9,p['accent'] if selected else p['raised'])
            text(c,x+28,356,f'{n}s',size=9,weight='bold',
                 color='white' if selected else p['text'],anchor='center')
            self.settings_hit.append(((x,341,x+57,371),'interval',n))

        text(c,25,394,'Giao diện',size=10,color=p['text'])
        for x,name,tag in ((176,'Sáng','light'),(257,'Tối','dark')):
            selected=self.config['theme']==tag
            rounded(c,x,387,x+72,418,9,p['accent'] if selected else p['raised'])
            text(c,x+36,402,name,size=9,weight='bold',
                 color='white' if selected else p['text'],anchor='center')
            self.settings_hit.append(((x,387,x+72,418),'theme',tag))
        self.draw_switch(c,25,449,'Luôn nổi trên cùng',self.config['always_on_top'],'top')
        self.draw_switch(c,25,496,'Khởi động cùng Windows',self.config['autostart'],'autostart')
        text(c,25,545,'Hiện Codex và Claude Code theo mặc định.',size=8,color=p['quiet'])
        text(c,25,560,'Tùy chọn được lưu trên máy Windows của bạn.',size=8,color=p['quiet'])

    def draw_switch(self,c,x,y,label_,enabled,action):
        p=self.colors
        text(c,x,y,label_,size=10,color=p['text'])
        l,t=285,y-4
        rounded(c,l,t,l+44,t+24,12,p['accent'] if enabled else p['track'])
        knob=(l+32 if enabled else l+12)
        c.create_oval(knob-8,t+4,knob+8,t+20,fill='white',outline='')
        self.settings_hit.append(((18,y-13,335,y+30),action,None))

    def settings_click(self,event):
        for (x1,y1,x2,y2),act,arg in self.settings_hit:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                if act=='close':
                    self.hide_settings()
                elif act=='opacity':
                    self.opacity_drag=True
                    self.move_opacity(event.x)
                elif act=='provider':
                    self.toggle_provider(arg)
                elif act=='interval':
                    self.set_interval(arg)
                elif act=='theme':
                    if self.config['theme']!=arg:
                        self.toggle_theme()
                elif act=='top':
                    self.toggle_top()
                elif act=='autostart':
                    self.toggle_autostart()
                break

    def settings_drag(self,event):
        if self.opacity_drag:
            self.move_opacity(event.x)

    def move_opacity(self,x):
        val=0.55 + clamp((x-26)/303,0,1)*0.45
        self.set_opacity(val)

    def capture(self,outfile):
        # Diagnostics only. No simulated usage is injected.
        try:
            from PIL import ImageGrab
            self.root.update()
            x,y=self.root.winfo_rootx(),self.root.winfo_rooty()
            ImageGrab.grab(bbox=(x,y,x+self.width,y+self.height),all_screens=True).save(outfile)
        except Exception as exc:
            print(f'Screenshot unavailable: {type(exc).__name__}',file=sys.stderr)

    def run(self):
        self.root.mainloop()


def main():
    if sys.platform=='win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
    parser=argparse.ArgumentParser(description='AI Limit Desk V5 — draggable window resizing')
    parser.add_argument('--screenshot',help='Capture a real-data screenshot for diagnostics')
    args=parser.parse_args()
    ensure_dir()
    LimitDesk(screenshot=args.screenshot).run()


if __name__=='__main__':
    main()
