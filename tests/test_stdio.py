"""End-to-end: launch the real server over stdio and talk MCP to it."""

import asyncio
import json
import os
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters


def test_stdio_roundtrip(fakes, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "kakaocli_path": fakes["kakaocli"],
        "kmsg_path": fakes["kmsg"],
        "send": {"enabled": True, "allowed_chats": {"alice": "Alice Kim"}},
    }))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kakao_mcp.server"],
        env={**os.environ, "KAKAO_MCP_CONFIG": str(cfg)},
    )

    async def run():
        async with Client(params) as client:
            tools = await client.list_tools()
            chats = await client.call_tool("kakao_list_chats", {"limit": 5})
            prep = await client.call_tool("kakao_prepare_send", {"chat": "alice", "message": "hi"})
            bad = await client.call_tool("kakao_prepare_send", {"chat": "bob", "message": "hi"})
            return tools, chats, prep, bad

    tools, chats, prep, bad = asyncio.run(run())
    assert len(tools.tools) == 6
    assert json.loads(chats.content[0].text)["chats"][0]["name"] == "Alice"
    assert json.loads(prep.content[0].text)["chat_name"] == "Alice Kim"
    assert bad.is_error and "not in send.allowed_chats" in bad.content[0].text
    # nothing was sent over the whole session
    assert not any(c[0] == "kmsg" for c in fakes["calls"]())
