"""Tests for telix.graphics_writer -- sixel/kitty graphics meta terminal."""

import types
import asyncio

import pyte
import pytest

from telix.fonts import font_registry
from telix.color_filter import PALETTES
from telix.graphics_renderer import DCS_END, DCS_START, APC_END, APC_START


class FakeWriter:
    """Captures data written to the 'terminal'."""

    def __init__(self):
        self.chunks: list[str | bytes] = []

    def write(self, data: str | bytes) -> None:
        self.chunks.append(data)

    @property
    def output(self) -> str:
        parts: list[str] = []
        for c in self.chunks:
            if isinstance(c, bytes):
                parts.append(c.decode("utf-8", errors="replace"))
            else:
                parts.append(c)
        return "".join(parts)

    def clear(self):
        self.chunks.clear()


class FakeRepl:
    metafont_columns = None
    metafont_rows = None
    ff_clears_screen = False
    clear_homes_cursor = False


class FakeCtx:
    """Minimal session context stub for GraphicsWriter."""

    def __init__(self, encoding="cp437"):
        self.encoding = encoding
        self.repl = FakeRepl()
        self.writer = None


class TestGraphicsWriter:

    @pytest.fixture(autouse=True)
    def _event_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        yield
        loop.close()

    def _make_writer(self, columns=80, rows=25, encoding="cp437", protocol="sixel"):
        from telix.graphics_writer import GraphicsWriter
        inner = FakeWriter()
        ctx = FakeCtx(encoding)
        return GraphicsWriter(inner, ctx, protocol, columns=columns, rows=rows), inner

    def test_init_switches_to_altscreen(self):
        gtw, inner = self._make_writer()
        init_output = inner.chunks[0]
        assert b"\033[?1049h" in init_output
        assert b"\033[?25l" in init_output
        assert b"\033[2J" in init_output

    def test_write_sixel(self):
        gtw, inner = self._make_writer(columns=10, rows=3, protocol="sixel")
        inner.clear()
        gtw.write(b"A")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.sleep(0.1))
        output = inner.output
        assert DCS_START in output
        assert DCS_END in output

    def test_write_kitty(self):
        gtw, inner = self._make_writer(columns=10, rows=3, protocol="kitty")
        inner.clear()
        gtw.write(b"X")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.sleep(0.1))
        output = inner.output
        assert APC_START in output
        assert APC_END in output

    def test_font_switch(self):
        gtw, inner = self._make_writer()
        assert gtw.font.font_id == 0
        gtw.write(b"\x1b[0;42 D")
        assert gtw.font.font_id == 42

    def test_font_switch_stripped_from_feed(self):
        gtw, inner = self._make_writer(columns=10, rows=3)
        gtw.write(b"\x1b[0;42 DA")
        assert gtw.screen.buffer[0][0].data == "A"

    def test_unknown_font_id_warns(self):
        gtw, inner = self._make_writer()
        gtw.write(b"\x1b[0;200 D")
        assert gtw.font.font_id == 0

    def test_virtual_size(self):
        gtw, _ = self._make_writer(columns=80, rows=25)
        assert gtw.virtual_size() == (25, 80)

    def test_dsr_sends_cpr_response(self):
        inner = FakeWriter()
        ctx = FakeCtx()
        fake_writer = FakeWriter()
        ctx.writer = fake_writer
        from telix.graphics_writer import GraphicsWriter
        gtw = GraphicsWriter(inner, ctx, "sixel", columns=80, rows=25)
        gtw.write(b"\x1b[6n")
        cpr = fake_writer.output
        assert cpr == "\x1b[1;1R"

    def test_dsr_stripped_from_pyte_input(self):
        gtw, inner = self._make_writer(columns=10, rows=3)
        gtw.write(b"\x1b[6nA")
        assert gtw.screen.buffer[0][0].data == "A"

    def test_csi_with_intermediate_stripped(self):
        gtw, inner = self._make_writer(columns=10, rows=3)
        gtw.write(b"\x1b[1 qA")
        assert gtw.screen.buffer[0][0].data == "A"

    def test_getattr_delegates(self):
        gtw, inner = self._make_writer()
        inner.custom_attr = "test"
        assert gtw.custom_attr == "test"

    def test_schedule_resize_applied_on_write(self, monkeypatch):
        monkeypatch.setattr(
            "telix.graphics_writer.terminal.get_terminal_size", lambda: (48, 160)
        )
        gtw, inner = self._make_writer(columns=80, rows=25)
        gtw.schedule_resize(160, 48)
        assert gtw._pending_resize == (160, 48)
        inner.clear()
        gtw.write(b"X")
        assert gtw._pending_resize is None

    def test_render_cooldown(self):
        gtw, inner = self._make_writer(columns=10, rows=3, protocol="sixel")
        inner.clear()
        gtw.write(b"A")
        # Render is async; advance loop so the task runs.
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.sleep(0))
        assert len(inner.chunks) > 0
        inner.clear()
        gtw.write(b"B")
        loop.run_until_complete(asyncio.sleep(0.05))
        assert len(inner.chunks) > 0

    def test_sixel_has_sync_brackets(self):
        """Sixel output includes DEC 2026 sync for atomic display."""
        gtw, inner = self._make_writer(columns=10, rows=3, protocol="sixel")
        inner.clear()
        gtw.write(b"X")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.sleep(0.1))
        output = inner.output
        assert "\033[?2026h" in output
        assert "\033[?2026l" in output

    def test_kitty_has_sync_brackets(self):
        """Kitty output includes DEC 2026 sync for flicker-free rendering."""
        gtw, inner = self._make_writer(columns=10, rows=3, protocol="kitty")
        inner.clear()
        gtw.write(b"X")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.sleep(0.1))
        output = inner.output
        assert "\033[?2026h" in output
        assert "\033[?2026l" in output

    def test_font_switch_changes_encoding(self):
        gtw, inner = self._make_writer()
        assert gtw.encoding == "cp437"
        gtw.write(b"\x1b[0;24 D")
        assert gtw.font.font_id == 24
        assert gtw.encoding == "iso-8859-1"

    def test_cleanup_restores_screen(self):
        gtw, inner = self._make_writer()
        inner.clear()
        gtw.cleanup()
        output = inner.output
        assert "\033[?25h" in output
        assert "\033[?1049l" in output


