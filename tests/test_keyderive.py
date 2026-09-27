import pytest

from kakao_mcp import keyderive
from kakao_mcp.runner import KakaoCli, ToolError

UUID = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"


def test_derivation_shapes_and_determinism():
    name = keyderive.database_name(12345, UUID)
    assert len(name) == 78 and all(c in "0123456789abcdef" for c in name)
    assert name == keyderive.database_name(12345, UUID)

    key = keyderive.secure_key(12345, UUID)
    assert len(key) == 256  # 128-byte PBKDF2 output, hex
    assert key == keyderive.secure_key(12345, UUID)
    assert key != keyderive.secure_key(22222, UUID)


def test_resolve_database_finds_matching_file(tmp_path, monkeypatch):
    monkeypatch.setattr(keyderive, "platform_uuid", lambda: UUID)
    db = tmp_path / keyderive.database_name(12345, UUID)
    db.write_bytes(b"")
    path, key = keyderive.resolve_database(12345, container=tmp_path)
    assert path == str(db)
    assert key == keyderive.secure_key(12345, UUID)


def test_resolve_database_wrong_user_id(tmp_path, monkeypatch):
    monkeypatch.setattr(keyderive, "platform_uuid", lambda: UUID)
    (tmp_path / keyderive.database_name(12345, UUID)).write_bytes(b"")
    with pytest.raises(RuntimeError, match="No KakaoTalk DB matches user_id 999"):
        keyderive.resolve_database(999, container=tmp_path)


def test_kakaocli_gets_db_and_key_and_resolves_once(fakes):
    calls = []

    def resolve():
        calls.append(1)
        return "/tmp/fake.db", "SECRETKEY"

    cli = KakaoCli(fakes["kakaocli"], resolve_db=resolve)
    cli.chats(5)
    cli.search("-x", 3)
    argv = fakes["calls"]()
    assert argv[0] == ["kakaocli", "chats", "--limit", "5", "--db", "/tmp/fake.db", "--key", "SECRETKEY", "--json"]
    assert argv[1][-2:] == ["--", "-x"]
    assert len(calls) == 1


def test_key_is_redacted_from_errors(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "1")
    cli = KakaoCli(fakes["kakaocli"], resolve_db=lambda: ("/tmp/fake.db", "SECRETKEY"))
    with pytest.raises(ToolError) as e:
        cli.chats(5)
    assert "SECRETKEY" not in str(e.value)
    assert "<redacted>" in str(e.value)


def test_resolve_failure_is_a_clear_tool_error(fakes):
    def boom():
        raise RuntimeError("No KakaoTalk DB matches user_id 1")

    cli = KakaoCli(fakes["kakaocli"], resolve_db=boom)
    with pytest.raises(ToolError, match="Could not locate the KakaoTalk DB"):
        cli.chats(5)
    assert fakes["calls"]() == []
