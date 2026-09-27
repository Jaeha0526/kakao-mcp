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
elif cmd in ("messages", "search"):
    # newest first, like kakaocli
    print(json.dumps([
        {{"id": 11, "chat_id": 1, "sender_id": 9, "type": "text", "timestamp": "2026-09-26T10:05:00Z", "is_from_me": True, "text": "second"}},
        {{"id": 10, "chat_id": 1, "sender_id": 8, "type": "text", "timestamp": "2026-09-26T10:00:00Z", "is_from_me": False, "sender": "Alice", "text": "first"}},
        {{"id": 12, "chat_id": 2, "sender_id": 7, "type": "text", "timestamp": "2026-09-26T09:00:00Z", "is_from_me": False, "sender": "Bob", "text": "hidden"}},
    ]))
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


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
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
