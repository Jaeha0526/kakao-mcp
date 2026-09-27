"""Two-step send gate.

``prepare`` validates a send against the allowlist and parks it under a
single-use token. ``confirm`` only succeeds when the token, chat alias and the
exact message all match what was prepared, so the client's permission prompt
for the confirm call shows the real recipient and text being approved.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Callable

from .config import SendConfig
from .runner import ToolError

MAX_PENDING = 10


@dataclass(frozen=True)
class PendingSend:
    token: str
    alias: str
    chat_name: str
    message: str
    expires_at: float


class SendGate:
    def __init__(self, config: SendConfig, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self._clock = clock
        self._pending: dict[str, PendingSend] = {}

    def _purge_expired(self) -> None:
        now = self._clock()
        for token in [t for t, p in self._pending.items() if p.expires_at <= now]:
            del self._pending[token]

    @staticmethod
    def normalize(message: str) -> str:
        if not isinstance(message, str):
            raise ToolError("message must be a string")
        return message.replace("\x00", "").strip()

    def prepare(self, alias: str, message: str) -> PendingSend:
        if not self.config.enabled:
            raise ToolError(
                "Sending is disabled. Set send.enabled=true in ~/.config/kakao-mcp/config.json "
                "to allow it."
            )
        alias = (alias or "").strip()
        chat_name = self.config.allowed_chats.get(alias)
        if chat_name is None:
            allowed = ", ".join(sorted(self.config.allowed_chats)) or "(none)"
            raise ToolError(
                f"Chat alias '{alias}' is not in send.allowed_chats. Allowed aliases: {allowed}"
            )
        msg = self.normalize(message)
        if not msg:
            raise ToolError("message must not be empty")
        if len(msg) > self.config.max_length:
            raise ToolError(f"message too long ({len(msg)} chars, max {self.config.max_length})")

        self._purge_expired()
        if len(self._pending) >= MAX_PENDING:
            raise ToolError(
                f"Too many pending sends ({MAX_PENDING}). Confirm or let them expire first."
            )
        pending = PendingSend(
            token=secrets.token_hex(4),
            alias=alias,
            chat_name=chat_name,
            message=msg,
            expires_at=self._clock() + self.config.confirm_ttl_seconds,
        )
        self._pending[pending.token] = pending
        return pending

    def take(self, token: str, alias: str, message: str) -> PendingSend:
        """Consume a pending send. The token is burned even if validation fails."""
        self._purge_expired()
        pending = self._pending.pop((token or "").strip(), None)
        if pending is None:
            raise ToolError("Unknown or expired token. Call kakao_prepare_send again.")
        if pending.alias != (alias or "").strip() or pending.message != self.normalize(message):
            raise ToolError(
                "chat/message do not match what was prepared. The token has been discarded; "
                "call kakao_prepare_send again."
            )
        return pending
