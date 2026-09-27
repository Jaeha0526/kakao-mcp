import asyncio
import json

import pytest

from kakao_mcp.config import load_config
from kakao_mcp.server import create_server

SEND_ON = {"send": {"enabled": True, "allowed_chats": {"alice": "Alice Kim"}, "max_length": 20}}


def call(server, name, **args):
    """Call a tool through the MCP layer; returns (is_error, payload)."""
    result = asyncio.run(server.call_tool(name, args))
    text = result.content[0].text
    if result.is_error:
        return True, text
    try:
        return False, json.loads(text)
    except ValueError:
        return True, text


def call_err(server, name, **args):
    try:
        is_err, payload = call(server, name, **args)
    except Exception as e:  # MCPServer.call_tool raises ToolError for tool failures
        return str(e)
    assert is_err, f"expected error, got {payload}"
    return payload


def test_list_chats_excludes_configured(make_config):
    server = create_server(make_config({"read": {"exclude_chat_ids": [2]}}))
    _, data = call(server, "kakao_list_chats")
    assert [c["chat_id"] for c in data["chats"]] == ["1"]


def test_read_messages_sorted_and_marked_untrusted(make_config, fakes):
    server = create_server(make_config())
    _, data = call(server, "kakao_read_messages", chat_id="1", since="7d", limit=10)
    assert [m["text"] for m in data["messages"]][:2] == ["hidden", "first"]  # oldest first
    assert data["messages"][2]["sender"] == "me"
    assert "untrusted" in data["notice"]
    argv = fakes["calls"]()[-1]
    assert argv == ["kakaocli", "messages", "--chat-id", "1", "--limit", "10", "--since", "7d", "--json"]
    assert "--key" not in argv


def test_read_rejects_bad_since_and_excluded(make_config, fakes):
    server = create_server(make_config({"read": {"exclude_chat_ids": [2]}}))
    assert "since" in call_err(server, "kakao_read_messages", chat_id="1", since="7d; rm -rf /")
    assert "excluded" in call_err(server, "kakao_read_messages", chat_id="2")
    assert fakes["calls"]() == []


def test_search_uses_option_terminator_and_filters(make_config, fakes):
    server = create_server(make_config({"read": {"exclude_chat_ids": [2]}}))
    _, data = call(server, "kakao_search", query="--limit")
    assert {r["chat_id"] for r in data["results"]} == {"1"}
    assert fakes["calls"]()[-1] == ["kakaocli", "search", "--limit", "20", "--json", "--", "--limit"]


def test_send_disabled_by_default(make_config, fakes):
    server = create_server(make_config())
    assert "disabled" in call_err(server, "kakao_prepare_send", chat="alice", message="hi")
    assert fakes["calls"]() == []


def test_send_requires_allowlisted_alias(make_config):
    server = create_server(make_config(SEND_ON))
    assert "not in send.allowed_chats" in call_err(server, "kakao_prepare_send", chat="Alice Kim", message="hi")


def test_send_length_limit(make_config):
    server = create_server(make_config(SEND_ON))
    assert "too long" in call_err(server, "kakao_prepare_send", chat="alice", message="x" * 21)


def test_prepare_then_confirm_sends_exactly_once(make_config, fakes):
    server = create_server(make_config(SEND_ON))
    _, prep = call(server, "kakao_prepare_send", chat="alice", message="  -hello  ")
    assert prep["message"] == "-hello"
    assert fakes["calls"]() == []  # prepare never sends

    _, sent = call(server, "kakao_confirm_send", token=prep["token"], chat="alice", message="-hello")
    assert sent["sent"] is True
    assert fakes["calls"]() == [["kmsg", "send", "--", "Alice Kim", "-hello"]]

    # token is single-use
    assert "expired" in call_err(server, "kakao_confirm_send", token=prep["token"], chat="alice", message="-hello")
    assert len(fakes["calls"]()) == 1


def test_confirm_with_changed_message_is_rejected_and_burns_token(make_config, fakes):
    server = create_server(make_config(SEND_ON))
    _, prep = call(server, "kakao_prepare_send", chat="alice", message="hello")
    assert "do not match" in call_err(server, "kakao_confirm_send", token=prep["token"], chat="alice", message="HELLO")
    assert "expired" in call_err(server, "kakao_confirm_send", token=prep["token"], chat="alice", message="hello")
    assert fakes["calls"]() == []


def test_token_expires():
    from kakao_mcp.config import SendConfig
    from kakao_mcp.runner import ToolError
    from kakao_mcp.sendgate import SendGate

    now = [1000.0]
    gate = SendGate(SendConfig(enabled=True, allowed_chats={"a": "A"}, confirm_ttl_seconds=60), clock=lambda: now[0])
    p = gate.prepare("a", "hi")
    now[0] += 61
    with pytest.raises(ToolError, match="expired"):
        gate.take(p.token, "a", "hi")


def test_pending_sends_are_capped():
    from kakao_mcp.config import SendConfig
    from kakao_mcp.runner import ToolError
    from kakao_mcp.sendgate import MAX_PENDING, SendGate

    gate = SendGate(SendConfig(enabled=True, allowed_chats={"a": "A"}))
    for i in range(MAX_PENDING):
        gate.prepare("a", f"m{i}")
    with pytest.raises(ToolError, match="Too many pending"):
        gate.prepare("a", "one more")


def test_missing_binary_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr("kakao_mcp.config.DEPS_BIN_DIR", tmp_path / "nope")
    monkeypatch.setenv("PATH", str(tmp_path))
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text("{}")
    server = create_server(load_config(cfg_path))
    assert "install-deps.sh" in call_err(server, "kakao_list_chats")


def test_tool_list():
    server = create_server(_empty_config())
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "kakao_list_chats", "kakao_read_messages", "kakao_search",
        "kakao_prepare_send", "kakao_confirm_send",
    }
    confirm = next(t for t in tools if t.name == "kakao_confirm_send")
    assert confirm.annotations.destructive_hint is True


def _empty_config():
    from kakao_mcp.config import Config, ReadConfig, SendConfig
    return Config(kakaocli_path=None, kmsg_path=None, read=ReadConfig(), send=SendConfig())


def test_large_chat_ids_round_trip_as_strings(make_config, fakes):
    big = "9007199254740993"  # > 2**53: a double would round it to ...156
    server = create_server(make_config())
    _, data = call(server, "kakao_read_messages", chat_id=big)
    assert data["chat_id"] == big
    assert fakes["calls"]()[-1][:4] == ["kakaocli", "messages", "--chat-id", big]


def test_read_rejects_non_numeric_chat_id(make_config, fakes):
    server = create_server(make_config())
    assert "chat_id must be" in call_err(server, "kakao_read_messages", chat_id="1; ls")
    assert fakes["calls"]() == []
