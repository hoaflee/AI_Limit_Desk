"""Download marks for other services from their domains; OpenAI is bundled.

No authentication data is ever sent to logo domains. Offline fallbacks display
service names until network icons are available. Official trademarks remain with owners.
"""
from __future__ import annotations

import io
from pathlib import Path

try:
    import requests
    from PIL import Image
except ImportError:
    requests = None
    Image = None

SOURCES = {
    'claude': 'https://claude.ai/favicon.ico',
    'antigravity': 'https://antigravity.google/assets/image/brand/antigravity-icon__full-color.png',
}


def download_logos(directory: Path, enabled: set[str] | None = None) -> list[str]:
    if requests is None or Image is None:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    installed = []
    for name, url in SOURCES.items():
        if enabled is not None and name not in enabled:
            continue
        path = directory / (name + '.png')
        if path.is_file():
            installed.append(name)
            continue
        try:
            response = requests.get(url, timeout=(3, 7), headers={'User-Agent': 'Mozilla/5.0 (compatible; DesktopUsageWidget/3.0)'},
                                    stream=True, allow_redirects=True)
            response.raise_for_status()
            if not response.url.startswith('https://'):
                continue
            content = b''
            for chunk in response.iter_content(32768):
                content += chunk
                if len(content) > 3_000_000:
                    raise ValueError('Image too large')
            with Image.open(io.BytesIO(content)) as im:
                im.seek(0)
                icon = im.convert('RGBA')
                icon.thumbnail((192, 192), Image.Resampling.LANCZOS)
                icon.save(path, format='PNG')
            installed.append(name)
        except (OSError, ValueError, Exception) as ex:
            # Logos are optional. Never block application startup or expose URLs with query secrets.
            _ = ex
    return installed
