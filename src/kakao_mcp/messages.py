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
FETCHABLE = {"photo", "photos", "video", "file", "voice", "emoticon"}
EMOTICON_PATH = re.compile(r"^\d{1,12}\.emot_\d{1,4}\.(?:png|gif|webp)$")

# Older-style KakaoTalk reactions ("공감"), stored in NTChatLogMeta type 1 as
# {code: count}. The names are a best-effort mapping (not verified against the
# app), so the raw code is returned too. Current KakaoTalk stores reactions as
# named emoticon reactions (type 2, e.g. "사랑", "엄지척"), which are exact.
REACTION_NAMES = {1: "heart", 2: "like", 3: "check", 4: "laugh", 5: "surprise", 6: "sad"}


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


def emoticon_path(a: dict[str, Any]) -> str | None:
    """The emoticon's image name (e.g. "4446261.emot_005.webp"), if well-formed."""
    path = a.get("emoticonItemPath") or a.get("path")
    return path if isinstance(path, str) and EMOTICON_PATH.match(path) else None


def attachment_summary(kind: str, raw: str | None, local_path: str | None) -> dict[str, Any] | None:
    """Describe a message's media without URLs; fetch it with kakao_get_attachment."""
    if kind not in FETCHABLE:
        return None
    a = load_attachment(raw)
    out: dict[str, Any] = {"kind": kind}
    if kind == "emoticon":
        path = emoticon_path(a)
        if not path:
            return None
        alt = a.get("alt")
        # Emoticon images are public store assets: they don't expire.
        return {"kind": "emoticon", "id": path.split(".")[0], **({"description": alt} if alt else {})}
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


def _json_obj(raw: Any) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def reactions_view(m: dict[str, Any]) -> list[dict[str, Any]]:
    """Reactions on a message: standard ones by type, and emoticon reactions by name."""
    out: list[dict[str, Any]] = []
    counts = _json_obj(m.get("reactions"))
    my_extra = _json_obj(m.get("my_reaction"))
    mine = _num(my_extra.get("my")) if isinstance(my_extra, dict) else None
    if isinstance(counts, dict):
        for code_str, count in sorted(counts.items(), key=lambda kv: str(kv[0])):
            code, n = _num(code_str), _num(count)
            if code is None or not n:
                continue
            out.append({
                "reaction": REACTION_NAMES.get(code, f"reaction_{code}"),
                "code": code,
                "count": n,
                "mine": code == mine,
            })
    emo = _json_obj(m.get("emoticon_reactions"))
    my_emo = _json_obj(m.get("my_emoticon_reactions"))
    my_ids = {r.get("o") for r in (my_emo or {}).get("myRx", []) if isinstance(r, dict)} if isinstance(my_emo, dict) else set()
    if isinstance(emo, dict):
        for r in emo.get("rx") or []:
            if not isinstance(r, dict) or not _num(r.get("c")):
                continue
            label = (r.get("a") or {}).get("ko") if isinstance(r.get("a"), dict) else None
            out.append({"emoticon": label or str(r.get("o")), "count": _num(r.get("c")), "mine": r.get("o") in my_ids})
    return out


def reply_view(kind: str, raw: str | None, my_user_id: int | None) -> dict[str, Any] | None:
    if kind != "reply":
        return None
    a = load_attachment(raw)
    src = _num(a.get("src_logId"))
    if src is None:
        return None
    view: dict[str, Any] = {"message_id": str(src)}
    text = a.get("src_message")
    if isinstance(text, str) and text:
        view["text"] = text if len(text) <= 200 else text[:200] + "…"
    if my_user_id is not None and _num(a.get("src_userId")) is not None:
        view["from_me"] = _num(a.get("src_userId")) == my_user_id
    return view


def message_view(
    m: dict[str, Any], *, include_chat: bool = False, my_user_id: int | None = None
) -> dict[str, Any]:
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
    reply = reply_view(kind, m.get("attachment"), my_user_id)
    if reply:
        view["reply_to"] = reply
    reactions = reactions_view(m)
    if reactions:
        view["reactions"] = reactions
    return view
