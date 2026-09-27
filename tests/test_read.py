"""Pagination, search and attachment tools, against the fake kakaocli `history`."""

import asyncio
import json
import stat

import pytest
from conftest import T0
from test_server import call, call_err

from kakao_mcp.server import create_server


def ids(page):
    return [m["message_id"] for m in page["messages"]]


def test_default_page_is_newest_oldest_first(make_config):
    server = create_server(make_config({"read": {"default_messages": 5}}))
    _, page = call(server, "kakao_read_messages", chat_id="1")
    assert ids(page) == ["120", "121", "122", "123", "124"]
    assert page["older_cursor"] and page["newer_cursor"] is None
    assert "untrusted" in page["notice"]


def test_walk_back_covers_every_message_exactly_once(make_config):
    server = create_server(make_config())
    seen, cursor = [], None
    while True:
        args = {"chat_id": "1", "limit": 4} | ({"before": cursor} if cursor else {})
        _, page = call(server, "kakao_read_messages", **args)
        seen = ids(page) + seen
        cursor = page["older_cursor"]
        if cursor is None:
            break
    # 110 and 111 share a timestamp; keyset on (sentAt, logId) must keep both
    assert seen == [str(i) for i in range(100, 125)]


def test_walk_forward_with_after(make_config):
    server = create_server(make_config())
    _, first = call(server, "kakao_read_messages", chat_id="1", limit=3, since="2026-01-01")
    _, back = call(server, "kakao_read_messages", chat_id="1", limit=3, before=first["older_cursor"])
    _, fwd = call(server, "kakao_read_messages", chat_id="1", limit=3, after=back["newer_cursor"])
    assert ids(fwd) == ids(first)
    assert fwd["older_cursor"] is not None


def test_limit_is_clamped_to_config_max(make_config, fakes):
    server = create_server(make_config({"read": {"default_messages": 2, "max_messages": 7}}))
    _, page = call(server, "kakao_read_messages", chat_id="1", limit=10_000)
    assert page["count"] == 7
    assert fakes["calls"]()[-1][:3] == ["kakaocli", "history", "--limit"]
    assert fakes["calls"]()[-1][3] == "8"  # one extra row to detect more pages


def test_since_until_dates(make_config, fakes):
    server = create_server(make_config())
    call(server, "kakao_read_messages", chat_id="1", since="2026-09-01", until="2026-09-30")
    argv = fakes["calls"]()[-1]
    since, until = int(argv[argv.index("--since") + 1]), int(argv[argv.index("--until") + 1])
    assert until - since == 30 * 86400 - 1  # until is inclusive to the end of that day


def test_bad_inputs_are_rejected_before_calling_kakaocli(make_config, fakes):
    server = create_server(make_config({"read": {"exclude_chat_ids": [2]}}))
    assert "Invalid cursor" in call_err(server, "kakao_read_messages", chat_id="1", before="1; rm")
    assert "not both" in call_err(server, "kakao_read_messages", chat_id="1", before="1.1", after="1.1")
    assert "Time must look like" in call_err(server, "kakao_read_messages", chat_id="1", since="yesterday")
    assert "hidden" in call_err(server, "kakao_read_messages", chat_id="2")
    assert "chat_id" in call_err(server, "kakao_read_messages", chat_id="1 OR 1")  # schema pattern rejects it
    assert fakes["calls"]() == []


def test_large_chat_ids_round_trip_as_strings(make_config, fakes):
    big = "9007199254740993"  # 2**53 + 1: a double would round it to ...992
    server = create_server(make_config())
    _, page = call(server, "kakao_read_messages", chat_id=big)
    assert page["chat_id"] == big
    argv = fakes["calls"]()[-1]
    assert argv[argv.index("--chat-id") + 1] == big


def test_media_messages_show_a_summary_without_urls(make_config):
    server = create_server(make_config())
    _, page = call(server, "kakao_read_messages", chat_id="1", limit=100)
    by_id = {m["message_id"]: m for m in page["messages"]}
    assert by_id["105"]["type"] == "photo"
    assert by_id["105"]["attachment"] == {"kind": "photo", "width": 800, "height": 600, "size": 1234, "expired": False}
    assert by_id["106"]["attachment"] == {"kind": "photos", "count": 2, "expired": False}
    assert by_id["107"]["attachment"]["name"] == "report.pdf"
    assert by_id["107"]["attachment"]["expired"] is True
    assert "http" not in json.dumps(page)
    assert by_id["101"]["sender"] == "me"


def test_search_paginates_and_skips_excluded_chats(make_config, fakes):
    server = create_server(make_config({"read": {"exclude_chat_ids": [2]}}))
    _, p1 = call(server, "kakao_search", query="lunch", limit=1)
    assert [r["message_id"] for r in p1["results"]] == ["120"]
    _, p2 = call(server, "kakao_search", query="lunch", limit=1, before=p1["next_cursor"])
    assert [r["message_id"] for r in p2["results"]] == ["103"]
    assert p2["next_cursor"] is None
    argv = fakes["calls"]()[-1]
    assert "--exclude-chat-id" in argv and "--contains=lunch" in argv


def test_search_text_starting_with_dash_stays_a_value(make_config, fakes):
    server = create_server(make_config())
    call(server, "kakao_search", query="--limit 1")
    assert "--contains=--limit 1" in fakes["calls"]()[-1]


def test_search_within_one_chat(make_config):
    server = create_server(make_config())
    _, res = call(server, "kakao_search", query="lunch", chat_id="2")
    assert {r["chat_id"] for r in res["results"]} == {"2"}


# ---------------------------------------------------------------- attachments


def _fake_downloader(payloads):
    calls = []

    def download(url, dest, max_bytes):
        calls.append(url)
        data, ctype = payloads[url]
        dest.write_bytes(data)
        return ctype

    download.calls = calls
    return download


def _tiny_png():
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )


def call_raw(server, name, **args):
    return asyncio.run(server.call_tool(name, args))


def test_get_photo_returns_an_image_and_caches(make_config, tmp_path):
    dl = _fake_downloader({"https://talk.kakaocdn.net/dn/a/i_photo.jpg": (_tiny_png(), "image/png")})
    cache = tmp_path / "cache"
    server = create_server(make_config(), downloader=dl, cache_dir=cache)
    result = call_raw(server, "kakao_get_attachment", chat_id="1", message_id="105")
    info = json.loads(result.content[0].text)
    assert info["kind"] == "photo" and info["path"].startswith(str(cache))
    assert result.content[1].type == "image"
    assert stat.S_IMODE((cache / "1").stat().st_mode) == 0o700
    call_raw(server, "kakao_get_attachment", chat_id="1", message_id="105")
    assert len(dl.calls) == 1  # second call served from cache


def test_get_second_photo_of_multi_photo(make_config, tmp_path):
    dl = _fake_downloader({"https://talk.kakaocdn.net/dn/b/2.png": (_tiny_png(), "image/png")})
    server = create_server(make_config(), downloader=dl, cache_dir=tmp_path)
    result = call_raw(server, "kakao_get_attachment", chat_id="1", message_id="106", index=1)
    assert json.loads(result.content[0].text)["path"].endswith("106_1.png")
    assert "between 0 and 1" in call_err(server, "kakao_get_attachment", chat_id="1", message_id="106", index=5)


def test_get_file_keeps_its_name_and_returns_no_image(make_config, tmp_path):
    dl = _fake_downloader({"https://talk.kakaocdn.net/dn/c/f": (b"%PDF-1.4", "application/pdf")})
    server = create_server(make_config(), downloader=dl, cache_dir=tmp_path)
    result = call_raw(server, "kakao_get_attachment", chat_id="1", message_id="107")
    info = json.loads(result.content[0].text)
    assert info["name"] == "report.pdf" and info["path"].endswith("107.pdf")
    assert len(result.content) == 1


def test_refuses_non_kakao_urls_and_text_messages(make_config, tmp_path):
    dl = _fake_downloader({})
    server = create_server(make_config(), downloader=dl, cache_dir=tmp_path)
    assert "unexpected location" in call_err(server, "kakao_get_attachment", chat_id="1", message_id="108")
    assert "no attachment" in call_err(server, "kakao_get_attachment", chat_id="1", message_id="100")
    assert "No message" in call_err(server, "kakao_get_attachment", chat_id="1", message_id="999")
    assert dl.calls == []


@pytest.mark.parametrize("url", [
    "https://talk.kakaocdn.net.evil.com/x",
    "https://evilkakao.com/x",
    "http://talk.kakaocdn.net/x",
])
def test_url_allowlist(url):
    from kakao_mcp.media import _check_url
    from kakao_mcp.runner import ToolError
    with pytest.raises(ToolError):
        _check_url(url)


def test_attachment_respects_exclusion(make_config, tmp_path):
    server = create_server(make_config({"read": {"exclude_chat_ids": [1]}}), cache_dir=tmp_path)
    assert "hidden" in call_err(server, "kakao_get_attachment", chat_id="1", message_id="105")


def test_time_constant():
    assert T0 == 1_790_000_000


def test_oldest_first_reads_from_the_beginning(make_config):
    server = create_server(make_config())
    _, first = call(server, "kakao_read_messages", chat_id="1", limit=4, oldest_first=True)
    assert ids(first) == ["100", "101", "102", "103"]
    assert first["older_cursor"] is None and first["newer_cursor"]
    seen, cursor = ids(first), first["newer_cursor"]
    while cursor:
        _, page = call(server, "kakao_read_messages", chat_id="1", limit=4, after=cursor)
        seen += ids(page)
        cursor = page["newer_cursor"]
    assert seen == [str(i) for i in range(100, 125)]


def test_oldest_first_respects_since(make_config):
    server = create_server(make_config())
    since = __import__("datetime").datetime.fromtimestamp(T0 + 20 * 60, tz=__import__("kakao_mcp.messages", fromlist=["KST"]).KST)
    _, page = call(server, "kakao_read_messages", chat_id="1", limit=2, oldest_first=True,
                   since=since.strftime("%Y-%m-%d %H:%M"))
    assert ids(page) == ["120", "121"]


def test_tool_schemas_describe_every_parameter(make_config):
    server = create_server(make_config())
    for tool in asyncio.run(server.list_tools()):
        for name, prop in tool.input_schema.get("properties", {}).items():
            assert prop.get("description"), f"{tool.name}.{name} has no description"


def test_send_description_reflects_config(make_config):
    def prepare_desc(extra):
        tools = asyncio.run(create_server(make_config(extra)).list_tools())
        return next(t for t in tools if t.name == "kakao_prepare_send").description

    assert "DISABLED" in prepare_desc({})
    on = prepare_desc({"send": {"enabled": True, "allowed_chats": {"me": "Jaeha"}}})
    assert '"me" (KakaoTalk chat "Jaeha")' in on and "DISABLED" not in on


def test_disabled_send_tells_agent_not_to_edit_config(make_config):
    server = create_server(make_config())
    assert "do not modify the config" in call_err(server, "kakao_prepare_send", chat="me", message="hi")
