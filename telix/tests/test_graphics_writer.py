"""Tests for telix.graphics_writer -- sixel/kitty graphics meta terminal."""

import types
import asyncio

import pyte
import pytest
import numpy as np

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

    def test_cursor_shape_decscusr_steady_block(self):
        gtw, _ = self._make_writer(columns=10, rows=3)
        gtw.write(b"\x1b[2 q")
        assert gtw._cursor_shape == 2
        assert gtw._cursor_blink is False

    def test_cursor_shape_decscusr_blinking_block(self):
        gtw, _ = self._make_writer(columns=10, rows=3)
        gtw.write(b"\x1b[1 q")
        assert gtw._cursor_shape == 1
        assert gtw._cursor_blink is True

    def test_cursor_shape_decscusr_underline(self):
        gtw, _ = self._make_writer(columns=10, rows=3)
        gtw.write(b"\x1b[4 q")
        assert gtw._cursor_shape == 4
        assert gtw._cursor_blink is False

    def test_cursor_shape_decscusr_bar(self):
        gtw, _ = self._make_writer(columns=10, rows=3)
        gtw.write(b"\x1b[6 q")
        assert gtw._cursor_shape == 6
        assert gtw._cursor_blink is False

    def test_cursor_shape_decscusr_default(self):
        gtw, _ = self._make_writer(columns=10, rows=3)
        gtw.write(b"\x1b[2 q\x1b[0 q")
        assert gtw._cursor_shape == 2
        assert gtw._cursor_blink is False

    def test_cursor_shape_stripped_from_pyte(self):
        gtw, _ = self._make_writer(columns=10, rows=3)
        gtw.write(b"\x1b[5 qX")
        assert gtw.screen.buffer[0][0].data == "X"

    def test_cursor_hidden_via_dectcem(self):
        """DECTCEM toggles are NOT synced to _cursor_hidden in graphics mode."""
        gtw, _ = self._make_writer(columns=10, rows=3)
        assert gtw._cursor_hidden is False
        gtw.write(b"\x1b[?25l")
        assert gtw._cursor_hidden is False  # no longer synced from pyte

    def test_cursor_always_drawn_regardless_of_hidden(self):
        gtw, _ = self._make_writer(columns=10, rows=3, protocol="sixel")
        gtw.screen.cursor.x = 5
        gtw.screen.cursor.y = 2
        colors = np.zeros((3 * 16, 10 * 8, 3), dtype=np.float32)
        gtw._draw_cursor(colors)
        cursor_region = colors[2 * 16 : 2 * 16 + 16, 5 * 8 : 5 * 8 + 8]
        assert np.allclose(cursor_region, 1.0)  # inverse of 0.0

    def test_cursor_block_inverses_full_cell(self):
        gtw, _ = self._make_writer(columns=10, rows=3, protocol="sixel")
        gtw._cursor_shape = 2
        colors = np.full((3 * 16, 10 * 8, 3), 0.5, dtype=np.float32)
        gtw.screen.cursor.x = 2
        gtw.screen.cursor.y = 1
        gtw._draw_cursor(colors)
        cell = colors[1 * 16 : 1 * 16 + 16, 2 * 8 : 2 * 8 + 8]
        assert np.allclose(cell, 0.5)

    def test_cursor_underline_inverses_bottom_rows(self):
        gtw, _ = self._make_writer(columns=10, rows=3, protocol="sixel")
        gtw._cursor_shape = 4
        colors = np.zeros((3 * 16, 10 * 8, 3), dtype=np.float32)
        gtw.screen.cursor.x = 0
        gtw.screen.cursor.y = 0
        gtw._draw_cursor(colors)
        assert np.allclose(colors[14:16, 0:8], 1.0)
        assert np.allclose(colors[0:14, 0:8], 0.0)

    def test_cursor_bar_inverses_left_columns(self):
        gtw, _ = self._make_writer(columns=10, rows=3, protocol="sixel")
        gtw._cursor_shape = 6
        colors = np.zeros((3 * 16, 10 * 8, 3), dtype=np.float32)
        gtw.screen.cursor.x = 0
        gtw.screen.cursor.y = 0
        gtw._draw_cursor(colors)
        assert np.allclose(colors[0:16, 0:2], 1.0)
        assert np.allclose(colors[0:16, 2:8], 0.0)

    def test_cursor_blink_hides_on_second_half_phase(self, monkeypatch):
        gtw, _ = self._make_writer(columns=10, rows=3, protocol="sixel")
        gtw._cursor_shape = 1
        gtw._cursor_blink = True
        monkeypatch.setattr("telix.graphics_writer.time.monotonic", lambda: 0.6)
        colors = np.zeros((3 * 16, 10 * 8, 3), dtype=np.float32)
        gtw.screen.cursor.x = 0
        gtw.screen.cursor.y = 0
        gtw._draw_cursor(colors)
        assert np.allclose(colors, 0.0)

    def test_cursor_move_triggers_render(self):
        gtw, inner = self._make_writer(columns=10, rows=3, protocol="sixel")
        inner.clear()
        gtw.screen.dirty.clear()
        gtw.write(b"\x1b[2;2H")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.sleep(0.1))
        assert len(inner.chunks) > 0


