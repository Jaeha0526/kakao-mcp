"""Turn kakaocli `history` rows into compact, model-friendly message views."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .runner import ToolError

KST = timezone(timedelta(hours=9))
CURSOR_PATTERN = re.compile(r"^\d{1,12}\.-?\d{1,20}$")
DURATION_PATTERN = re.compile(r"^(\d{1,5})([mhdw])$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$")

# KakaoTalk message type codes as stored in the DB. kakaocli's own enum
# mislabels several of these (e.g. 6 is an emoticon, 18 is a file).
TYPE_NAMES = {
    0: "system",
    1: "text",
    2: "photo",
    3: "video",
    4: "contact",
    5: "voice",
    6: "emoticon",
    12: "emoticon",
    14: "vote",
    16: "location",
    17: "profile",
    18: "file",
    20: "emoticon",
    22: "emoticon",
    23: "search",
    24: "post",
    26: "reply",
    27: "photos",
    51: "call",
    71: "bot",
}
DELETED_FLAG = 16384
# Message types whose content kakao_get_attachment can fetch.
FETCHABLE = {"photo", "photos", "video", "file", "voice"}


def type_name(code: int) -> str:
    if code >= DELETED_FLAG:
        return "deleted"
    return TYPE_NAMES.get(code, f"type_{code}")


def check_cursor(cursor: str | None) -> str | None:
    if cursor is None or str(cursor).strip() == "":
        return None
    text = str(cursor).strip()
    if not CURSOR_PATTERN.match(text):
        raise ToolError("Invalid cursor. Pass a cursor exactly as returned by a previous call.")
    return text


def parse_time(value: str | None, *, end_of_day: bool = False) -> int | None:
    """'7d' / '12h' / '30m' / '2w' (relative to now) or 'YYYY-MM-DD[ HH:MM[:SS]]' in KST."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    m = DURATION_PATTERN.match(text)
    if m:
        seconds = int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
        return int(time.time()) - seconds
    if DATE_PATTERN.match(text):
        dt = datetime.fromisoformat(text.replace(" ", "T")).replace(tzinfo=KST)
        if end_of_day and len(text) == 10:
            dt += timedelta(days=1, seconds=-1)
        return int(dt.timestamp())
    raise ToolError("Time must look like 7d / 12h / 30m / 2w, or YYYY-MM-DD[ HH:MM] (KST).")


def _num(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_attachment(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def attachment_summary(kind: str, raw: str | None, local_path: str | None) -> dict[str, Any] | None:
    """Describe a message's media without URLs; fetch it with kakao_get_attachment."""
    if kind not in FETCHABLE:
        return None
    a = load_attachment(raw)
    out: dict[str, Any] = {"kind": kind}
    if kind == "photo":
        out.update(width=_num(a.get("w")), height=_num(a.get("h")), size=_num(a.get("s")))
    elif kind == "photos":
        out["count"] = len(a.get("imageUrls") or [])
    elif kind == "video":
        out.update(duration_sec=_num(a.get("d")), size=_num(a.get("s")))
    elif kind == "file":
        out.update(name=a.get("name"), size=_num(a.get("size") or a.get("s")))
    elif kind == "voice":
        out["duration_sec"] = _num(a.get("d"))
    expire = _num(a.get("expire"))
    if expire:
        out["expired"] = expire < int(time.time() * 1000)
    if local_path:
        out["saved_locally"] = True
    return {k: v for k, v in out.items() if v is not None}


def message_view(m: dict[str, Any], *, include_chat: bool = False) -> dict[str, Any]:
    kind = type_name(int(m.get("type_code", -1)))
    view: dict[str, Any] = {
        "message_id": str(m["log_id"]),
        "time": datetime.fromtimestamp(int(m["sent_at"]), tz=KST).isoformat(),
        "sender": "me" if m.get("is_from_me") else (m.get("sender") or "(unknown)"),
        "type": kind,
        "text": m.get("text") or None,
    }
    if include_chat:
        view["chat_id"] = str(m["chat_id"])
    summary = attachment_summary(kind, m.get("attachment"), m.get("local_file_path"))
    if summary:
        view["attachment"] = summary
    return view
