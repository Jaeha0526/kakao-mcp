"""Thin subprocess wrappers around the kakaocli and kmsg binaries.

Arguments are always passed as a list (never through a shell), and the DB key
is never passed on the command line: kakaocli derives it itself.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Callable

from mcp.server.mcpserver.exceptions import ToolError as _SdkToolError

SINCE_PATTERN = re.compile(r"^\d{1,4}[smhdw]$")


class ToolError(_SdkToolError):
    """An anticipated failure; the MCP SDK shows its message to the client."""


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return text


def _run(
    binary: str | None, name: str, args: list[str], timeout: float, secrets: tuple[str, ...] = ()
) -> str:
    if not binary:
        raise ToolError(
            f"{name} binary not found. Run scripts/install-deps.sh or set "
            f"'{name}_path' in the config."
        )
    if not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
        raise ToolError(f"{name} binary is not an executable file: {binary}")
    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"{name} timed out after {timeout:.0f}s") from e
    if result.returncode != 0:
        detail = _redact((result.stderr or result.stdout).strip(), secrets)[-2000:]
        raise ToolError(f"{name} failed (exit {result.returncode}): {detail}")
    return result.stdout


class KakaoCli:
    """Read-only access to the local KakaoTalk DB via kakaocli.

    By default kakaocli locates the DB and derives its key itself. If
    ``resolve_db`` is given (config has ``user_id``), it is called once and its
    (db_path, key) are passed with --db/--key; the key is redacted from errors.
    """

    def __init__(
        self,
        binary: str | None,
        timeout: float = 60.0,
        resolve_db: Callable[[], tuple[str, str]] | None = None,
    ):
        self.binary = binary
        self.timeout = timeout
        self._resolve_db = resolve_db
        self._db: tuple[str, str] | None = None

    def _db_args(self) -> tuple[list[str], tuple[str, ...]]:
        if self._resolve_db is None:
            return [], ()
        if self._db is None:
            try:
                self._db = self._resolve_db()
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                raise ToolError(f"Could not locate the KakaoTalk DB: {e}") from e
        path, key = self._db
        return ["--db", path, "--key", key], (key,)

    def _json(self, args: list[str], positional: list[str] | None = None) -> list[dict[str, Any]]:
        db_args, secrets = self._db_args()
        argv = [*args, *db_args, "--json"]
        if positional:
            # "--" stops option parsing so user text starting with "-" stays positional.
            argv += ["--", *positional]
        out = _run(self.binary, "kakaocli", argv, self.timeout, secrets)
        out = out.strip()
        if not out:
            return []
        try:
            data = json.loads(out)
        except ValueError as e:
            raise ToolError(f"kakaocli returned non-JSON output: {out[:300]}") from e
        if not isinstance(data, list):
            raise ToolError("kakaocli returned unexpected JSON (expected a list)")
        return data

    def chats(self, limit: int) -> list[dict[str, Any]]:
        return self._json(["chats", "--limit", str(limit)])

    def messages(self, chat_id: int, since: str | None, limit: int) -> list[dict[str, Any]]:
        args = ["messages", "--chat-id", str(int(chat_id)), "--limit", str(limit)]
        if since is not None:
            if not SINCE_PATTERN.match(since):
                raise ToolError("since must look like 30m, 12h, 7d or 2w")
            args += ["--since", since]
        return self._json(args)

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        return self._json(["search", "--limit", str(limit)], positional=[query])


class Kmsg:
    """UI-automation sender via kmsg. Only plain-text send is exposed."""

    def __init__(self, binary: str | None, timeout: float = 45.0):
        self.binary = binary
        self.timeout = timeout

    def send(self, chat_name: str, message: str) -> str:
        # "--" stops option parsing so a message starting with "-" is sent as text.
        return _run(self.binary, "kmsg", ["send", "--", chat_name, message], self.timeout)
