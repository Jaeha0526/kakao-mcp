"""Fetch a message's photo / video / file / voice attachment.

Uses the message's local copy when KakaoTalk already saved one; otherwise
downloads from the URL in the attachment JSON, restricted to Kakao's CDN over
HTTPS. Files are cached under ~/Library/Caches/kakao-mcp (owner-only).
"""

from __future__ import annotations

import mimetypes
import os
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .messages import load_attachment, type_name
from .runner import ToolError

DEFAULT_CACHE_DIR = Path.home() / "Library" / "Caches" / "kakao-mcp" / "attachments"
ALLOWED_HOST_SUFFIXES = (".kakaocdn.net", ".kakao.com")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tiff"}
PREVIEW_MAX_PX = 1568
CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
}

# (url, dest, max_bytes) -> content type
Downloader = Callable[[str, Path, int], str | None]


@dataclass
class Fetched:
    kind: str
    path: Path
    name: str | None
    size: int
    is_image: bool
    preview: Path | None = None


def _check_url(url: str) -> None:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not any(host.endswith(s) or host == s[1:] for s in ALLOWED_HOST_SUFFIXES):
        raise ToolError(f"Refusing to download from an unexpected location ({p.scheme}://{host}).")


def http_download(url: str, dest: Path, max_bytes: int) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            _check_url(resp.geturl())  # after redirects
            length = resp.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ToolError(f"Attachment is {int(length):,} bytes, over the {max_bytes:,}-byte limit.")
            total = 0
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                while chunk := resp.read(1 << 16):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ToolError(f"Attachment exceeds the {max_bytes:,}-byte limit.")
                    f.write(chunk)
            os.replace(tmp, dest)
            return (resp.headers.get("Content-Type") or "").split(";")[0].strip() or None
    except urllib.error.HTTPError as e:
        raise ToolError(
            f"Download failed (HTTP {e.code}). The link may have expired; opening the message "
            "in KakaoTalk usually re-downloads it."
        ) from e
    except urllib.error.URLError as e:
        raise ToolError(f"Download failed: {e.reason}") from e
    finally:
        if tmp.exists():
            tmp.unlink()


def _make_preview(src: Path) -> Path | None:
    """Downscale to a JPEG the model can view, using macOS `sips`."""
    dest = src.with_name(src.stem + ".preview.jpg")
    if dest.exists():
        return dest
    try:
        subprocess.run(
            ["/usr/bin/sips", "-Z", str(PREVIEW_MAX_PX), "-s", "format", "jpeg", str(src), "--out", str(dest)],
            capture_output=True, timeout=60, check=True, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return dest if dest.exists() else None


def fetch_attachment(
    row: dict[str, Any],
    index: int = 0,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    max_bytes: int = 200 * 1024 * 1024,
    downloader: Downloader = http_download,
) -> Fetched:
    kind = type_name(int(row.get("type_code", -1)))
    a = load_attachment(row.get("attachment"))
    name: str | None = None

    if kind in ("photo", "video", "voice"):
        urls = [a.get("url")]
    elif kind == "photos":
        urls = list(a.get("imageUrls") or [])
    elif kind == "file":
        urls = [a.get("url")]
        name = a.get("name")
    else:
        raise ToolError(f"Message {row.get('log_id')} is a '{kind}' message and has no attachment to fetch.")

    if not 0 <= index < len(urls):
        raise ToolError(f"index must be between 0 and {len(urls) - 1} for this message.")

    local = row.get("local_file_path")
    if local and index == 0 and Path(local).is_file():
        path = Path(local)
    else:
        url = urls[index]
        if not url:
            raise ToolError("This message has no download link.")
        _check_url(url)
        folder = cache_dir / str(int(row["chat_id"]))
        folder.mkdir(parents=True, exist_ok=True)
        for d in (cache_dir, folder):
            os.chmod(d, 0o700)
        stem = f"{int(row['log_id'])}" + (f"_{index}" if kind == "photos" else "")
        existing = [p for p in folder.glob(stem + ".*") if not p.name.endswith((".part", ".preview.jpg"))]
        if existing:
            path = existing[0]
        else:
            suffix = Path(name).suffix if name else Path(urlparse(url).path).suffix
            tmp = folder / (stem + ".download")
            ctype = downloader(url, tmp, max_bytes)
            if not suffix or len(suffix) > 6:
                suffix = CONTENT_TYPE_EXT.get(ctype or "", "") or mimetypes.guess_extension(ctype or "") or ".bin"
            path = folder / (stem + suffix.lower())
            os.replace(tmp, path)

    is_image = kind in ("photo", "photos") or path.suffix.lower() in IMAGE_EXTS
    return Fetched(
        kind=kind,
        path=path,
        name=name or path.name,
        size=path.stat().st_size,
        is_image=is_image,
        preview=_make_preview(path) if is_image else None,
    )
