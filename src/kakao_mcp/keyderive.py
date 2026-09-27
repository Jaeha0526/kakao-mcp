"""Derive the KakaoTalk Mac DB path and SQLCipher key.

Port of kakaocli's KeyDerivation.swift (pinned commit in scripts/install-deps.sh).
Needed when kakaocli cannot auto-detect the user id; the id is then set in the
config and the derived key is passed to kakaocli with --db/--key.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
from pathlib import Path

CONTAINER = (
    Path.home()
    / "Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Application Support/com.kakao.KakaoTalkMac"
)
_HEX78 = re.compile(r"^[0-9a-f]{78}(\.db)?$")


def _pbkdf2(password: str, salt: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000, dklen=128)


def hashed_device_uuid(uuid: str) -> str:
    data = uuid.encode()
    return base64.b64encode(hashlib.sha1(data).digest() + hashlib.sha256(data).digest()).decode()


def database_name(user_id: int, uuid: str) -> str:
    hawawa = ".".join([".", "F", str(user_id), "A", "F", uuid[::-1], ".", "|"])
    salt = hashed_device_uuid(uuid)[::-1]
    return _pbkdf2(hawawa, salt).hex()[28:28 + 78]


def secure_key(user_id: int, uuid: str) -> str:
    parts = ["A", hashed_device_uuid(uuid), "|", "F", uuid[:5], "H", str(user_id), "|", uuid[7:]]
    hawawa = "F".join(parts)
    salt = uuid[int(len(uuid) * 0.3):]
    return _pbkdf2(hawawa[::-1], salt).hex()


def platform_uuid() -> str:
    out = subprocess.run(
        ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout
    m = re.search(r'"IOPlatformUUID" = "([0-9A-F-]{36})"', out)
    if not m:
        raise RuntimeError("Could not read IOPlatformUUID from ioreg")
    return m.group(1)


def resolve_database(user_id: int, container: Path = CONTAINER) -> tuple[str, str]:
    """Return (db_path, key) for the given user id on this Mac."""
    uuid = platform_uuid()
    name = database_name(user_id, uuid)
    for candidate in (container / name, container / f"{name}.db"):
        if candidate.is_file():
            return str(candidate), secure_key(user_id, uuid)
    found = [e for e in os.listdir(container) if _HEX78.match(e)] if container.is_dir() else []
    raise RuntimeError(
        f"No KakaoTalk DB matches user_id {user_id} on this Mac "
        f"({len(found)} DB file(s) present). Check user_id in the config."
    )
