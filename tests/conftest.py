import json
import os
import stat
import sys
from pathlib import Path

import pytest

FAKE_KAKAOCLI = r'''#!{python}
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(["kakaocli", *sys.argv[1:]]) + "\n")
if os.environ.get("FAKE_FAIL"):
    print("error with args: " + " ".join(sys.argv[1:]), file=sys.stderr)
    sys.exit(1)
cmd = sys.argv[1]
if cmd == "chats":
    print(json.dumps([
        {{"id": 1, "type": "direct", "display_name": "Alice", "member_count": 2, "unread_count": 0}},
        {{"id": 2, "type": "group", "display_name": "Secret", "member_count": 5, "unread_count": 3}},
    ]))
elif cmd == "history":
    # Mirrors the kakaocli fork's `history`: filters, (sentAt, logId) keyset order.
    opts, it = {{}}, iter(sys.argv[2:])
    for a in it:
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
        else:
            k, v = a[2:], next(it)
        opts.setdefault(k, []).append(v)
    one = lambda k: opts.get(k, [None])[0]
    rows = json.load(open(os.environ["FAKE_DB"]))
    def key(m): return (m["sent_at"], m["log_id"])
    def cur(c): s, l = c.split("."); return (int(s), int(l))
    if one("chat-id"): rows = [m for m in rows if m["chat_id"] == int(one("chat-id"))]
    ex = {{int(x) for x in opts.get("exclude-chat-id", [])}}
    rows = [m for m in rows if m["chat_id"] not in ex]
    if one("log-id"): rows = [m for m in rows if m["log_id"] == int(one("log-id"))]
    if one("since"): rows = [m for m in rows if m["sent_at"] >= int(one("since"))]
    if one("until"): rows = [m for m in rows if m["sent_at"] <= int(one("until"))]
    if one("before"): rows = [m for m in rows if key(m) < cur(one("before"))]
    if one("after"): rows = [m for m in rows if key(m) > cur(one("after"))]
    if one("contains"): rows = [m for m in rows if one("contains") in (m.get("text") or "")]
    rows.sort(key=key, reverse=one("after") is None)
    rows = rows[: int(one("limit"))]
    for m in rows: m["cursor"] = f"{{m['sent_at']}}.{{m['log_id']}}"
    print(json.dumps(rows))
else:
    sys.exit(2)
'''

FAKE_KMSG = r'''#!{python}
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(["kmsg", *sys.argv[1:]]) + "\n")
print("Message sent successfully")
'''


def _write_exe(path: Path, body: str) -> str:
    path.write_text(body.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


T0 = 1_790_000_000
PHOTO = json.dumps({"url": "https://talk.kakaocdn.net/dn/a/i_photo.jpg", "w": 800, "h": 600, "s": 1234,
                    "expire": 9_999_999_999_999})
PHOTOS = json.dumps({"imageUrls": ["https://talk.kakaocdn.net/dn/b/1.jpg", "https://talk.kakaocdn.net/dn/b/2.png"],
                     "expire": 9_999_999_999_999})
FILE = json.dumps({"url": "https://talk.kakaocdn.net/dn/c/f", "name": "report.pdf", "size": 99, "expire": 1})
EVIL = json.dumps({"url": "http://evil.example/x.jpg"})


def fake_messages():
    """Chat 1: 25 messages (log 100..124); logs 110 and 111 share a second.
    Chat 2: 3 messages that config may exclude."""
    rows = []
    for i in range(25):
        rows.append({
            "log_id": 100 + i, "chat_id": 1, "sender_id": 9 if i % 2 else 8,
            "sender": "Alice", "type_code": 1, "sent_at": T0 + (i if i != 11 else 10) * 60,
            "is_from_me": bool(i % 2), "text": f"msg {i}" + (" lunch" if i in (3, 20) else ""),
        })
    for log_id, code, att in ((105, 2, PHOTO), (106, 27, PHOTOS), (107, 18, FILE), (108, 2, EVIL)):
        r = rows[log_id - 100]
        r.update(type_code=code, attachment=att, text=None)
    rows[109 - 100].update(type_code=26, text="reply text",
                           attachment=json.dumps({"src_logId": 102, "src_userId": 42, "src_message": "msg 2"}))
    rows[110 - 100].update(reactions=json.dumps({"2": 3, "4": 1}), my_reaction=json.dumps({"my": 2}),
                           emoticon_reactions=json.dumps({"rx": [{"k": 2, "o": "1200509_029", "c": 2, "a": {"ko": "사랑"}},
                                                                 {"k": 2, "o": "1200509_021", "c": 0, "a": {"ko": "엄지척"}}]}),
                           my_emoticon_reactions=json.dumps({"myRx": [{"k": 2, "o": "1200509_029"}]}))
    rows[112 - 100].update(type_code=20, text=None, attachment=json.dumps(
        {"name": "(이모티콘)", "alt": "카카오 이모티콘", "path": "4446261.emot_005.webp",
         "emoticonItemPath": "4446261.emot_005.webp"}))
    rows[113 - 100].update(type_code=12, text="hi", attachment=json.dumps(
        {"name": "(emoticon)", "path": "2212560.emot_051.png", "emoticonItemPath": "2212560.emot_051.png"}))
    rows[114 - 100].update(type_code=12, text=None, attachment=json.dumps({"path": "../../etc/passwd"}))
    for i in range(3):
        rows.append({"log_id": 200 + i, "chat_id": 2, "sender_id": 7, "sender": "Bob", "type_code": 1,
                     "sent_at": T0 + i * 60, "is_from_me": False, "text": "hidden lunch"})
    return rows


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    db = tmp_path / "fake_db.json"
    db.write_text(json.dumps(fake_messages()))
    monkeypatch.setenv("FAKE_DB", str(db))
    kakaocli = _write_exe(tmp_path / "kakaocli", FAKE_KAKAOCLI)
    kmsg = _write_exe(tmp_path / "kmsg", FAKE_KMSG)

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return {"kakaocli": kakaocli, "kmsg": kmsg, "calls": calls, "dir": tmp_path}


@pytest.fixture
def make_config(fakes):
    from kakao_mcp.config import load_config

    def _make(extra: dict | None = None):
        data = {"kakaocli_path": fakes["kakaocli"], "kmsg_path": fakes["kmsg"]}
        data.update(extra or {})
        path = fakes["dir"] / "config.json"
        path.write_text(json.dumps(data))
        return load_config(path)

    return _make
