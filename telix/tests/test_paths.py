"""Tests for telix.paths -- XDG directory resolution and atomic write helpers."""

import json
import os

import pytest

from telix import paths


def test_progressbars_path():
    result = paths.progressbars_path()
    assert result.endswith("progressbars.json")
    assert paths.CONFIG_DIR in result


def test_xdg_config_dir():
    result = paths.xdg_config_dir()
    assert str(result) == paths.CONFIG_DIR


def test_xdg_data_dir():
    result = paths.xdg_data_dir()
    assert str(result) == paths.DATA_DIR


def test_safe_terminal_size_returns_string():
    result = paths.safe_terminal_size()
    assert isinstance(result, str)
    assert "x" in result or result == "?"


def test_safe_terminal_size_oserror_fallback(monkeypatch):
    monkeypatch.setattr(os, "get_terminal_size", lambda: (_ for _ in ()).throw(OSError("no tty")))
    assert paths.safe_terminal_size() == "?"


class TestBytesSafeEncoder:
    def test_encodes_utf8_bytes(self):
        data = {"key": b"hello"}
        result = json.dumps(data, cls=paths.BytesSafeEncoder)
        assert '"hello"' in result

    def test_encodes_non_utf8_bytes_as_hex(self):
        data = {"key": b"\xff\xfe"}
        result = json.dumps(data, cls=paths.BytesSafeEncoder)
        assert '"fffe"' in result

    def test_raises_for_non_serializable(self):
        with pytest.raises(TypeError):
            json.dumps({"key": object()}, cls=paths.BytesSafeEncoder)


def test_atomic_json_write(tmp_path):
    filepath = str(tmp_path / "test.json")
    data = {"hello": "world", "num": 42}
    paths.atomic_json_write(filepath, data)
    with open(filepath, encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == data


def test_atomic_json_write_with_bytes(tmp_path):
    filepath = str(tmp_path / "test.json")
    data = {"key": b"value"}
    paths.atomic_json_write(filepath, data)
    with open(filepath, encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == {"key": "value"}


def test_atomic_write_happy_path(tmp_path):
    filepath = str(tmp_path / "subdir" / "test.txt")
    paths.atomic_write(filepath, "hello world")
    with open(filepath, encoding="utf-8") as f:
        assert f.read() == "hello world"


def test_atomic_write_error_cleans_temp(tmp_path, monkeypatch):
    filepath = str(tmp_path / "test.txt")
    original_replace = os.replace

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        paths.atomic_write(filepath, "content")
    assert not os.path.exists(filepath)


def test_safe_session_slug_deterministic():
    slug1 = paths.safe_session_slug("host:23")
    slug2 = paths.safe_session_slug("host:23")
    assert slug1 == slug2
    assert len(slug1) == 12


def test_safe_session_slug_different_keys():
    assert paths.safe_session_slug("a:1") != paths.safe_session_slug("b:2")


def test_history_path():
    result = paths.history_path("host:23")
    assert result.startswith(paths.DATA_DIR)
    assert "history-" in result


def test_gmcp_snapshot_path():
    result = paths.gmcp_snapshot_path("host:23")
    assert result.endswith(".json")
    assert "gmcp-" in result


def test_chat_path():
    result = paths.chat_path("host:23")
    assert result.endswith(".json")
    assert "chat-" in result
