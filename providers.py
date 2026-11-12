"""Read-only quota collectors for Codex, Claude Code, Antigravity, and JSON feeds.

No OAuth token, CSRF value, browser cookie, or API credential is saved or logged.
The Anthropic OAuth and Antigravity local APIs are undocumented interfaces.
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from token_history import parse_codex_server_daily

try:
    import psutil
except ImportError:
    psutil = None
try:
    import requests
    from urllib3.exceptions import InsecureRequestWarning
except ImportError:
    requests = None
    InsecureRequestWarning = None

UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


def percent(value: Any) -> float:
    try:
        n = float(value)
        return max(0.0, min(100.0, round(n, 1))) if math.isfinite(n) else 0.0
    except (TypeError, ValueError):
        return 0.0


def dt_parse(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), UTC)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass
class Quota:
    label: str
    left: float
    reset_at: datetime | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {"label": self.label, "left": self.left,
                "reset_at": self.reset_at.isoformat() if self.reset_at else None,
                "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict) -> "Quota":
        return cls(str(data.get("label", "Quota")), percent(data.get("left")),
                   dt_parse(data.get("reset_at")), str(data.get("detail", "")))


@dataclass
class Snapshot:
    id: str
    title: str
    source: str
    quotas: list[Quota] = field(default_factory=list)
    status: str = "ok"  # ok | error | stale
    message: str = ""
    updated: datetime | None = None
    daily_tokens: dict[str, int] = field(default_factory=dict)
    daily_source: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "source": self.source,
                "quotas": [q.to_dict() for q in self.quotas], "status": self.status,
                "updated": self.updated.isoformat() if self.updated else None,
                "daily_tokens": self.daily_tokens, "daily_source": self.daily_source}

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        return cls(id=str(d.get("id", "custom")),
                   title=str(d.get("title", "Other")),
                   source=str(d.get("source", "")),
                   quotas=[Quota.from_dict(x) for x in d.get("quotas", []) if isinstance(x, dict)],
                   status="stale", message="Đang kiểm tra kết nối…",
                   updated=dt_parse(d.get("updated")),
                   daily_tokens={str(k): int(v) for k,v in (d.get("daily_tokens") or {}).items()
                                 if isinstance(v, int) and v >= 0} if isinstance(d.get("daily_tokens"), dict) else {},
                   daily_source=str(d.get("daily_source", "")))


class ProviderError(RuntimeError):
    pass


class RateLimitError(ProviderError):
    """The upstream API answered HTTP 429; the caller should back off."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def parse_retry_after(value: Any) -> float | None:
    """Seconds from a Retry-After header; None when absent, zero or not numeric."""
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def friendly_window(minutes: Any) -> str:
    try:
        minutes = int(minutes)
    except (ValueError, TypeError):
        return "Usage"
    if minutes == 300:
        return "5-hour limit"
    if minutes == 10080:
        return "Weekly limit"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}-day limit"
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}-hour limit"
    return f"{minutes}-minute limit"


def parse_codex(payload: dict) -> list[Quota]:
    """Parse account/rateLimits/read result (including multiple limit buckets)."""
    response = payload.get("rateLimitsByLimitId")
    if not isinstance(response, dict) or not response:
        primary = payload.get("rateLimits")
        response = {"codex": primary} if isinstance(primary, dict) else {}
    results: list[tuple[int, Quota]] = []
    for bucket_id, bucket in response.items():
        if not isinstance(bucket, dict):
            continue
        prefix = str(bucket.get("limitName") or bucket_id or "")
        prefix = "" if prefix.lower() in ("codex", "chatgpt", "") else prefix + " · "
        for key in ("primary", "secondary"):
            row = bucket.get(key)
            if not isinstance(row, dict) or row.get("usedPercent") is None:
                continue
            mins = row.get("windowDurationMins")
            try:
                sort_mins = int(mins)
            except (TypeError, ValueError):
                sort_mins = 999999
            label = prefix + friendly_window(mins)
            results.append((sort_mins, Quota(label, percent(100 - float(row["usedPercent"])),
                                                 dt_parse(row.get("resetsAt")))))
    results.sort(key=lambda v: (v[0], v[1].label.lower()))
    return [x[1] for x in results]


def _claude_quota_key(label: str) -> str:
    """Normalize old/new Anthropic names into the same logical quota bucket.

    session = five_hour; weekly = seven_day (all models). Model-specific weekly
    buckets retain separate keys so Opus/Sonnet/Fable are never lost.
    """
    raw = str(label).casefold().strip()
    name = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    if name in {'session', 'current session', 'five hour', 'five hour session',
                '5 hour', '5 hour session', '5h', '5h session', 'five hour limit',
                '5 hour limit', 'five hour usage'}:
        return 'five_hour'
    if name in {'weekly', 'week', 'weekly limit', 'weekly all models', 'seven day',
                'seven day limit', '7 day', '7 day limit', 'all models', 'seven day all models'}:
        return 'seven_day'
    for prefix in ('weekly ', 'seven day ', '7 day '):
        if name.startswith(prefix):
            suffix = name[len(prefix):].strip()
            if suffix in ('', 'all models', 'limit'):
                return 'seven_day'
            return 'seven_day_' + suffix.replace(' ', '_')
    return 'other_' + name.replace(' ', '_')


def dedupe_claude_quotas(quotas: list[Quota]) -> list[Quota]:
    """Deduplicate semantic aliases even when loaded from a V4 cache.

    Earlier canonical entries take precedence; distinct scoped/model quotas
    are preserved. Not based on equal percentage/reset (those may drift).
    """
    out: list[Quota] = []
    seen: set[str] = set()
    for quota in quotas:
        identity = _claude_quota_key(quota.label)
        if identity not in seen:
            seen.add(identity)
            out.append(quota)
    return out


def parse_claude(payload: dict) -> list[Quota]:
    labels = {
        "five_hour": "5-hour session", "seven_day": "Weekly \u00b7 all models",
        "seven_day_opus": "Weekly \u00b7 Opus", "seven_day_sonnet": "Weekly \u00b7 Sonnet",
        "seven_day_fable": "Weekly \u00b7 Fable",
    }
    quotas: list[Quota] = []
    for key, value in payload.items():
        if not isinstance(value, dict) or (key not in labels and not key.startswith('seven_day_')):
            continue
        if value.get('utilization') is None:
            continue
        label = labels.get(key, 'Weekly \u00b7 ' + key.removeprefix('seven_day_').replace('_', ' ').title())
        quotas.append(Quota(label, percent(100 - float(value['utilization'])),
                            dt_parse(value.get('resets_at'))))
    # Newer /limits payloads can repeat the same quota using generic aliases.
    for item in payload.get('limits', []) if isinstance(payload.get('limits'), list) else []:
        if not isinstance(item, dict):
            continue
        used = item.get('percent', item.get('utilization'))
        if used is None:
            continue
        scope = item.get('scope') or {}
        model = scope.get('model') if isinstance(scope, dict) else {}
        modelname = str(model.get('display_name') or model.get('name') or '') if isinstance(model, dict) else ''
        group = str(item.get('group') or item.get('name') or 'Other limit')
        if modelname:
            label = 'Weekly \u00b7 ' + modelname
        elif _claude_quota_key(group) == 'five_hour':
            label = '5-hour session'
        elif _claude_quota_key(group) == 'seven_day':
            label = 'Weekly \u00b7 all models'
        else:
            label = group
        quotas.append(Quota(label, percent(100-float(used)), dt_parse(item.get('resets_at'))))
    return dedupe_claude_quotas(quotas)


def parse_antigravity(payload: dict) -> tuple[list[Quota], str]:
    user = payload.get("userStatus") or {}
    config = (user.get("cascadeModelConfigData") or {}) if isinstance(user, dict) else {}
    models = config.get("clientModelConfigs") or payload.get("clientModelConfigs") or []
    plan = (((user.get("planStatus") or {}).get("planInfo") or {}).get("planName") or "") if isinstance(user, dict) else ""
    results: list[Quota] = []
    names: set[str] = set()
    for m in models:
        if not isinstance(m, dict):
            continue
        q = m.get("quotaInfo") or {}
        if not isinstance(q, dict) or q.get("remainingFraction") is None:
            continue
        name = str(m.get("label") or m.get("displayName") or "Unnamed model")
        if name.lower() in names:
            continue
        names.add(name.lower())
        results.append(Quota(name, percent(float(q["remainingFraction"]) * 100),
                             dt_parse(q.get("resetTime"))))
    return results, str(plan)


def codex() -> Snapshot:
    title, ident = "OpenAI · Codex / Work", "codex"
    exe = os.environ.get("AI_LIMIT_CODEX_PATH") or shutil.which("codex")
    if not exe:
        # Windows npm shims are not always in the PATH when pythonw starts from HKCU Run.
        folders = [Path(os.environ.get("APPDATA", "")) / "npm",
                   Path.home() / "AppData" / "Roaming" / "npm",
                   Path.home() / ".local" / "bin"]
        for folder in folders:
            for name in ("codex.exe", "codex.cmd", "codex"):
                candidate = folder / name
                if candidate.is_file():
                    exe = str(candidate)
                    break
            if exe:
                break
    if not exe:
        raise ProviderError("Không thấy Codex CLI; cài Codex CLI và đăng nhập trên chính Windows này.")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = None
    try:
        command = [exe, "app-server"]
        if sys.platform == "win32" and Path(exe).suffix.lower() in (".cmd", ".bat"):
            # Run npm's Codex shim without invoking a user shell profile.
            quoted = subprocess.list2cmdline([exe, "app-server"])
            command = ["cmd.exe", "/d", "/s", "/c", '"' + quoted + '"']
        proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace",
                                bufsize=1, creationflags=flags)
        lines: queue.Queue[Any] = queue.Queue()

        def reader():
            try:
                for line in proc.stdout:
                    try:
                        lines.put(json.loads(line))
                    except (ValueError, TypeError):
                        continue
            except (OSError, ValueError):
                pass
            finally:
                lines.put(None)

        threading.Thread(target=reader, daemon=True).start()

        def send(data: dict):
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(data, separators=(",", ":")) + "\n")
            proc.stdin.flush()

        def receive(expected_id: int, timeout=7.0) -> dict:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    item = lines.get(timeout=max(0.05, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if item is None:
                    raise ProviderError("Codex app-server đã dừng bất ngờ.")
                if isinstance(item, dict) and item.get("id") == expected_id:
                    if item.get("error"):
                        raise ProviderError("Codex trả về lỗi xác thực hoặc giao thức. Kiểm tra codex login / phiên bản CLI.")
                    return item.get("result") or {}
            raise ProviderError("Codex app-server không phản hồi trong thời gian cho phép.")

        send({"method": "initialize", "id": 1,
              "params": {"clientInfo": {"name": "ai_limit_desk", "title": "AI Limit Desk", "version": "1.0.0"}}})
        receive(1)
        send({"method": "initialized", "params": {}})
        send({"method": "account/rateLimits/read", "id": 2})
        data = receive(2, 9)
        windows = parse_codex(data)
        if not windows:
            raise ProviderError("Chưa có dữ liệu rate limit. Đăng nhập Codex bằng tài khoản ChatGPT (không phải API key).")
        daily = {}
        try:
            send({"method": "account/usage/read", "id": 3})
            daily = parse_codex_server_daily(receive(3, 4.0))
        except (ProviderError, OSError, ValueError):
            # Supported only by some Codex account types/builds. Local logs are fallback.
            pass
        return Snapshot(ident, title, "Codex app-server · shared usage", windows,
                        updated=now_utc(), daily_tokens=daily,
                        daily_source="OpenAI account/usage/read (UTC)" if daily else "")
    except OSError as ex:
        raise ProviderError("Không mở được Codex app-server; kiểm tra đường dẫn CLI.") from ex
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
            for stream in (proc.stdin, proc.stdout):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass


def claude() -> Snapshot:
    if requests is None:
        raise ProviderError("Thiếu requests; hãy chạy start_windows.bat để cài dependencies.")
    claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    path = Path(os.environ.get("CLAUDE_CREDENTIALS_PATH") or (claude_dir / ".credentials.json"))
    if not path.is_file():
        raise ProviderError("Không thấy ~/.claude/.credentials.json. Đăng nhập Claude Code bằng /login.")
    try:
        cred = json.loads(path.read_text(encoding="utf-8"))
        token = (cred.get("claudeAiOauth") or {}).get("accessToken")
    except (OSError, ValueError, TypeError):
        raise ProviderError("Không thể đọc tệp đăng nhập Claude Code.") from None
    if not token:
        raise ProviderError("Claude Code chưa có accessToken OAuth; API key không hỗ trợ kiểm tra quota gói.")
    try:
        response = requests.get("https://api.anthropic.com/api/oauth/usage", timeout=(3, 7), headers={
            "Authorization": "Bearer " + str(token),
            "Accept": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
        })
    except requests.RequestException:
        raise ProviderError("Không kết nối được máy chủ Claude; kiểm tra mạng.") from None
    if response.status_code in (401, 403):
        raise ProviderError("Phiên đăng nhập Claude hết hạn hoặc bị từ chối. Mở Claude Code rồi thử lại.")
    if response.status_code == 429:
        raise RateLimitError("Bị giới hạn tần suất (HTTP 429)",
                             parse_retry_after(response.headers.get("Retry-After")))
    if not response.ok:
        raise ProviderError(f"Claude trả về HTTP {response.status_code}; API OAuth có thể đã thay đổi.")
    try:
        quotas = parse_claude(response.json())
    except (ValueError, TypeError, KeyError):
        raise ProviderError("Cấu trúc dữ liệu Claude đã thay đổi.") from None
    if not quotas:
        raise ProviderError("Tài khoản không trả về quota thuê bao. Hãy kiểm tra /usage trong Claude Code.")
    return Snapshot("claude", "Claude Code", "Anthropic OAuth usage · experimental", quotas, updated=now_utc())


def _ls_candidates() -> list[tuple[int, str, list[int]]]:
    """Find only Antigravity processes; port probing stays on loopback."""
    if psutil is None:
        raise ProviderError("Cần cài psutil để đọc tiến trình Antigravity.")
    candidates = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "exe"]):
        try:
            name = (p.info.get("name") or "").lower()
            if "language_server" not in name:
                continue
            parts = p.info.get("cmdline") or []
            command = " ".join(parts)
            path = p.info.get("exe") or ""
            if "antigravity" not in (command + " " + path).lower():
                continue
            match = re.search(r"--csrf_token(?:=|\s+)([^\s\"']+)", command)
            csrf = match.group(1) if match else ""
            ports = []
            try:
                for c in p.net_connections(kind="tcp"):
                    if c.status == psutil.CONN_LISTEN and c.laddr:
                        ports.append(int(c.laddr.port))
            except (psutil.AccessDenied, OSError):
                ports = []
            if not ports and sys.platform == "win32":
                ports = _windows_listening_ports(p.pid)
            if ports:
                candidates.append((p.pid, csrf, sorted(set(ports))))
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError):
            continue
    return candidates


def _windows_listening_ports(pid: int) -> list[int]:
    # Only interpolating an integer PID; never passing an account token to shell.
    ps_command = (f"Get-NetTCPConnection -State Listen -OwningProcess {int(pid)} "
                  "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalPort | "
                  "ConvertTo-Json -Compress")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        data = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_command],
                              text=True, capture_output=True, timeout=5, creationflags=flags)
        if data.returncode != 0 or not data.stdout.strip():
            return []
        result = json.loads(data.stdout.strip())
        return [int(n) for n in (result if isinstance(result, list) else [result]) if n is not None]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def antigravity() -> Snapshot:
    if requests is None:
        raise ProviderError("Thiếu requests; hãy chạy start_windows.bat.")
    candidates = _ls_candidates()
    if not candidates:
        raise ProviderError("Chưa thấy Antigravity language server. Hãy mở IDE Antigravity và đăng nhập.")
    route = "/exa.language_server_pb.LanguageServerService/"
    headers_base = {"Content-Type": "application/json", "Connect-Protocol-Version": "1"}
    body = {"metadata": {"ideName": "antigravity", "extensionName": "antigravity",
                         "ideVersion": "unknown", "locale": "en"}}
    for _, token, ports in candidates[:3]:
        headers = dict(headers_base)
        if token:
            headers["x-codeium-csrf-token"] = token
        for port in ports[:8]:
            if port <= 0 or port > 65535:
                continue
            for proto in ("https", "http"):
                base = f"{proto}://127.0.0.1:{port}"
                for endpoint in ("GetUserStatus", "GetCommandModelConfigs"):
                    # Scoped certificate bypass: ONLY a loopback URL to the locally running IDE.
                    try:
                        import warnings
                        with warnings.catch_warnings():
                            if proto == "https" and InsecureRequestWarning:
                                warnings.simplefilter("ignore", InsecureRequestWarning)
                            r = requests.post(base + route + endpoint, headers=headers,
                                              json=body, timeout=(0.35, 1.1),
                                              verify=(proto != "https"), proxies={"http": "", "https": ""})
                    except requests.RequestException:
                        break  # protocol doesn't answer; try next protocol
                    if not r.ok:
                        continue
                    try:
                        quotas, plan = parse_antigravity(r.json())
                    except (ValueError, TypeError, KeyError):
                        continue
                    if quotas:
                        title = "Google Antigravity" + (f" · {plan}" if plan else "")
                        return Snapshot("antigravity", title,
                                        "Antigravity language server · localhost", quotas,
                                        updated=now_utc())
    raise ProviderError("Tìm thấy Antigravity nhưng API quota chưa phản hồi; có thể IDE vừa cập nhật.")


def load_custom(path: Path) -> list[Snapshot]:
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ProviderError("custom_usage.json không đúng định dạng JSON.") from None
    if not isinstance(d, dict) or not isinstance(d.get("providers", []), list):
        raise ProviderError("custom_usage.json cần có mảng providers.")
    out = []
    for index, p in enumerate(d.get("providers", [])[:15]):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or f"Custom {index + 1}")[:55]
        qlist = []
        for q in (p.get("windows") if isinstance(p.get("windows"), list) else [])[:30]:
            if not isinstance(q, dict):
                continue
            if q.get("remaining_percent") is not None:
                left = percent(q["remaining_percent"])
            elif q.get("used_percent") is not None:
                left = percent(100 - float(q["used_percent"]))
            else:
                continue
            qlist.append(Quota(str(q.get("label") or "Quota")[:90], left,
                               dt_parse(q.get("reset_at"))))
        daymap = {}
        raw = p.get("daily_tokens") or {}
        if isinstance(raw, dict):
            for day, n in raw.items():
                try:
                    datetime.strptime(str(day), "%Y-%m-%d")
                    count = int(n)
                    if 0 <= count < 10**15:
                        daymap[str(day)] = count
                except (TypeError, ValueError, OverflowError):
                    pass
        if qlist or daymap:
            out.append(Snapshot("custom_" + str(index), name,
                                "Custom JSON · file local", qlist, updated=now_utc(),
                                daily_tokens=daymap,
                                daily_source="Custom JSON (daily_tokens)" if daymap else ""))
    return out
