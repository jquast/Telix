"""Tests for telix.raw_transport -- raw TCP reader/writer adapters."""

import asyncio
from unittest.mock import MagicMock

import pytest

from telix.raw_transport import RawReader, RawWriter, NullOptionSet


class TestRawReader:
    """RawReader provides an async read() interface fed by feed_data/feed_eof."""

    @pytest.mark.asyncio
    async def test_read_returns_fed_data(self):
        reader = RawReader()
        reader.feed_data("hello")
        result = await reader.read(1024)
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_read_blocks_until_data(self):
        reader = RawReader()
        loop = asyncio.get_event_loop()
        loop.call_later(0.01, reader.feed_data, "delayed")
        result = await asyncio.wait_for(reader.read(1024), timeout=1.0)
        assert result == "delayed"

    @pytest.mark.asyncio
    async def test_read_returns_empty_at_eof(self):
        reader = RawReader()
        reader.feed_eof()
        result = await reader.read(1024)
        assert result == ""

    @pytest.mark.asyncio
    async def test_at_eof_false_initially(self):
        reader = RawReader()
        assert reader.at_eof() is False

    @pytest.mark.asyncio
    async def test_at_eof_true_after_feed_eof(self):
        reader = RawReader()
        reader.feed_eof()
        assert reader.at_eof() is True

    @pytest.mark.asyncio
    async def test_multiple_feeds_returned_in_order(self):
        reader = RawReader()
        reader.feed_data("aaa")
        reader.feed_data("bbb")
        assert await reader.read(1024) == "aaa"
        assert await reader.read(1024) == "bbb"

    @pytest.mark.asyncio
    async def test_wakeup_waiter_unblocks_read(self):
        reader = RawReader()
        loop = asyncio.get_event_loop()
        loop.call_later(0.01, reader._wakeup_waiter)
        result = await asyncio.wait_for(reader.read(1024), timeout=1.0)
        assert result == ""

    @pytest.mark.asyncio
    async def test_wakeup_waiter_does_not_set_eof(self):
        reader = RawReader()
        reader._wakeup_waiter()
        await reader.read(1024)
        assert reader.at_eof() is False

    @pytest.mark.asyncio
    async def test_read_returns_empty_when_eof_and_buffer_empty(self):
        reader = RawReader()
        reader.feed_eof()
        await reader.read(1024)
        result = await reader.read(1024)
        assert result == ""

    @pytest.mark.asyncio
    async def test_feed_data_string_passthrough(self):
        reader = RawReader()
        reader.feed_data("hello \xe9")
        result = await reader.read(1024)
        assert result == "hello \xe9"


class TestRawWriter:
    """RawWriter wraps an asyncio StreamWriter for sending."""

    def _make_writer(self, **kwargs) -> RawWriter:
        stream_writer = MagicMock()
        return RawWriter(stream_writer=stream_writer, **kwargs)

    def test_write_calls_stream_writer(self):
        writer = self._make_writer()
        writer.encoding = "utf-8"
        writer.write("hello\r\n")
        writer._stream_writer.write.assert_called_once_with(b"hello\r\n")

    def test_write_encodes_with_connection_encoding(self):
        writer = self._make_writer()
        writer.encoding = "latin-1"
        writer.write("\xe9")
        writer._stream_writer.write.assert_called_once_with(b"\xe9")

    def test_write_decodes_bytes_input(self):
        writer = self._make_writer()
        writer.encoding = "utf-8"
        writer.write(b"hello")
        writer._stream_writer.write.assert_called_once_with(b"hello")

    def test_write_no_stream_is_noop(self):
        writer = RawWriter(stream_writer=None)
        writer.write("hello")

    def test_raw_write_passes_bytes_through(self):
        writer = self._make_writer()
        writer._write(b"\xff\xfe")
        writer._stream_writer.write.assert_called_once_with(b"\xff\xfe")

    def test_raw_write_no_stream_is_noop(self):
        writer = RawWriter(stream_writer=None)
        writer._write(b"\xff\xfe")

    def test_close_closes_stream(self):
        writer = self._make_writer()
        writer.close()
        writer._stream_writer.close.assert_called_once()

    def test_close_sets_is_closing(self):
        writer = self._make_writer()
        assert writer.is_closing() is False
        writer.close()
        assert writer.is_closing() is True

    def test_close_idempotent(self):
        writer = self._make_writer()
        writer.close()
        writer.close()
        writer._stream_writer.close.assert_called_once()

    def test_will_echo_default_false(self):
        writer = RawWriter()
        assert writer.will_echo is False

    def test_mode_default_local(self):
        writer = RawWriter()
        assert writer.mode == "local"

    def test_get_extra_info_peername(self):
        writer = RawWriter(peername=("bbs.example.com", 23))
        assert writer.get_extra_info("peername") == ("bbs.example.com", 23)

    def test_get_extra_info_unknown_returns_default(self):
        writer = RawWriter()
        assert writer.get_extra_info("nonexistent", "fallback") == "fallback"

    def test_stream_writer_property_setter(self):
        writer = RawWriter()
        assert writer.stream_writer is None
        mock_sw = MagicMock()
        writer.stream_writer = mock_sw
        assert writer.stream_writer is mock_sw

    def test_null_option_set_always_false(self):
        nos = NullOptionSet()
        assert nos.enabled(b"\x55") is False
        assert nos.enabled(None) is False

    def test_local_option_is_null_option_set(self):
        writer = RawWriter()
        assert isinstance(writer.local_option, NullOptionSet)

    def test_remote_option_is_null_option_set(self):
        writer = RawWriter()
        assert isinstance(writer.remote_option, NullOptionSet)

    def test_client_flag_is_true(self):
        writer = RawWriter()
        assert writer.client is True

    def test_send_naws_is_noop(self):
        writer = RawWriter()
        writer._send_naws()
