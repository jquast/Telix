"""Tests for telix.metaterminal -- pyte-based octant meta terminal."""

import types
import asyncio

import pyte
import pytest

from telix.fonts import font_registry
from telix.metafont import OCTANT, CELLS_PER_CHAR_X, CELLS_PER_CHAR_Y
from telix.color_filter import PALETTES
from telix.metaterminal import PYTE_COLOR_NAMES, MetaTerminalWriter, pyte_color_to_rgb


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
    """Minimal session context stub for MetaTerminalWriter."""

    def __init__(self, encoding="cp437"):
        self.encoding = encoding
        self.repl = FakeRepl()
        self.writer = None


VGA = PALETTES["vga"]


class TestBBSScreen:

    def test_ed2_homes_cursor(self):
        from telix.metaterminal import BBSScreen
        s = BBSScreen(80, 25)
        st = pyte.Stream(s)
        st.feed("\033[5;10Htext\033[2J")
        assert s.cursor.x == 0
        assert s.cursor.y == 0

    def test_ed0_preserves_cursor(self):
        from telix.metaterminal import BBSScreen
        s = BBSScreen(80, 25)
        st = pyte.Stream(s)
        st.feed("\033[5;10H\033[0J")
        assert s.cursor.x == 9
        assert s.cursor.y == 4

    def test_decawm_disabled_by_default(self):
        from telix.metaterminal import BBSScreen
        s = BBSScreen(80, 25)
        assert pyte.modes.DECAWM not in s.mode

    def test_decawm_not_re_enabled_by_set_mode(self):
        from telix.metaterminal import BBSScreen
        s = BBSScreen(80, 25)
        st = pyte.Stream(s)
        st.feed("\033[?7h")
        assert pyte.modes.DECAWM not in s.mode

    def test_decawm_not_re_enabled_by_ris(self):
        from telix.metaterminal import BBSScreen
        s = BBSScreen(80, 25)
        st = pyte.Stream(s)
        st.feed("\033c")
        assert pyte.modes.DECAWM not in s.mode

    def test_full_line_no_unwanted_wrap(self):
        """Write 80 'X' characters on an 80-column screen.

        With DECAWM disabled, all 80 chars stay on the same line.
        With DECAWM enabled, the 80th char wraps to line 2.
        """
        from telix.metaterminal import BBSScreen
        s = BBSScreen(80, 25)
        st = pyte.Stream(s)
        st.feed("X" * 80)
        assert s.buffer[0][0].data == "X"
        assert s.buffer[0][79].data == "X"
        assert s.buffer[1].get(0) is None

    def test_full_line_crlf_no_double_spacing(self):
        """Write 80 'X' + CR+LF, then more text.

        Without DECAWM, the CR+LF produces one newline, text at line 2.
        With DECAWM, the 80th char wraps first (creating line 2), then
        CR+LF pushes to line 3, creating a blank line.
        """
        from telix.metaterminal import BBSScreen
        s = BBSScreen(80, 25)
        st = pyte.Stream(s)
        st.feed("X" * 80 + "\r\nABCD")
        assert s.buffer[0][0].data == "X"
        assert s.buffer[0][79].data == "X"
        assert s.buffer[1][0].data == "A"
        assert s.buffer[1][1].data == "B"
        assert s.buffer[1][2].data == "C"
        assert s.buffer[1][3].data == "D"

    def test_80th_column_character_remains_last_on_line(self):
        """79 then a character: both on line 0, no wrap to line 1."""
        from telix.metaterminal import BBSScreen
        s = BBSScreen(80, 25)
        st = pyte.Stream(s)
        st.feed("A" * 79 + "B")
        assert s.buffer[0][78].data == "A"
        assert s.buffer[0][79].data == "B"
        assert s.buffer[1].get(0) is None


class TestPyteColorToRgb:

    def test_default_fg(self):
        assert pyte_color_to_rgb("default", False, True, VGA) == VGA[7]

    def test_default_bg(self):
        assert pyte_color_to_rgb("default", False, False, VGA) == VGA[0]

    def test_named_red(self):
        assert pyte_color_to_rgb("red", False, True, VGA) == VGA[1]

    def test_bold_brightens_fg(self):
        assert pyte_color_to_rgb("red", True, True, VGA) == VGA[9]

    def test_bold_does_not_brighten_bg(self):
        assert pyte_color_to_rgb("red", True, False, VGA) == VGA[1]

    def test_hex_color(self):
        assert pyte_color_to_rgb("ff8000", False, True, VGA) == (255, 128, 0)

    def test_256_color_index(self):
        result = pyte_color_to_rgb("196", False, True, VGA)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_unknown_returns_default(self):
        assert pyte_color_to_rgb("nonsense", False, True, VGA) == VGA[7]


class TestMetaTerminalWriter:

    def _make_writer(self, columns=80, rows=25, encoding="cp437"):
        inner = FakeWriter()
        ctx = FakeCtx(encoding)
        return MetaTerminalWriter(inner, ctx, columns=columns, rows=rows), inner

    def test_init_switches_to_altscreen(self):
        mtw, inner = self._make_writer()
        init_output = inner.chunks[0]
        assert b"\033[?1049h" in init_output
        assert b"\033[?25l" in init_output
        assert b"\033[2J" in init_output

    def test_write_ascii(self):
        mtw, inner = self._make_writer(columns=10, rows=3)
        inner.clear()
        mtw.write(b"A")
        output = inner.output
        assert "\033[" in output

    def test_dirty_tracking(self):
        mtw, inner = self._make_writer(columns=10, rows=3)
        inner.clear()
        mtw.write(b"X")
        first = inner.output
        inner.clear()
        mtw.write(b"Y")
        second = inner.output
        assert len(second) < len(first) or second != first

    def test_font_switch(self):
        mtw, inner = self._make_writer()
        assert mtw.font.font_id == 0
        mtw.write(b"\x1b[0;42 D")
        assert mtw.font.font_id == 42
        assert mtw.font.name == "Topaz (Amiga)"

    def test_font_switch_stripped_from_feed(self):
        mtw, inner = self._make_writer(columns=10, rows=3)
        mtw.write(b"\x1b[0;42 DA")
        assert mtw.screen.buffer[0][0].data == "A"

    def test_unknown_font_id_warns(self):
        mtw, inner = self._make_writer()
        mtw.write(b"\x1b[0;200 D")
        assert mtw.font.font_id == 0

    def test_virtual_size(self):
        mtw, _ = self._make_writer(columns=80, rows=25)
        assert mtw.virtual_size() == (25, 80)

    def test_resize(self, monkeypatch):
        monkeypatch.setattr("telix.metaterminal.terminal.get_terminal_size", lambda: (50, 160))
        mtw, _ = self._make_writer(columns=80, rows=25)
        mtw.resize(160, 50)
        assert mtw.columns == 40
        assert mtw.rows == 12
        assert mtw._real_cols == 160
        assert mtw._real_rows == 50

    def test_resize_too_small_ignored(self):
        mtw, _ = self._make_writer(columns=80, rows=25)
        mtw.resize(2, 2)
        assert mtw.columns == 80
        assert mtw.rows == 25

    def test_color_rendering(self):
        mtw, inner = self._make_writer(columns=10, rows=3)
        inner.clear()
        mtw.write(b"\x1b[31mR")
        output = inner.output
        r, g, b = PALETTES["vga"][1]
        assert f"38;2;{r};{g};{b}" in output

    def test_bold_color_rendering(self):
        mtw, inner = self._make_writer(columns=10, rows=3)
        inner.clear()
        mtw.write(b"\x1b[1;31mR")
        output = inner.output
        r, g, b = PALETTES["vga"][9]
        assert f"38;2;{r};{g};{b}" in output

    def test_sgr_reset_before_sync_end(self):
        mtw, inner = self._make_writer(columns=10, rows=3)
        inner.clear()
        mtw.write(b"A")
        output = inner.output
        assert "\033[0m" in output
        assert output.endswith("\033[?2026l")

    def test_schedule_resize_applied_on_write(self, monkeypatch):
        monkeypatch.setattr("telix.metaterminal.terminal.get_terminal_size", lambda: (48, 160))
        mtw, inner = self._make_writer(columns=80, rows=25)
        mtw.schedule_resize(160, 48)
        assert mtw._pending_resize == (160, 48)
        inner.clear()
        mtw.write(b"X")
        assert mtw._pending_resize is None
        assert mtw._real_cols == 160
        assert mtw._real_rows == 48

    def test_csi_with_intermediate_stripped(self):
        mtw, inner = self._make_writer(columns=10, rows=3)
        mtw.write(b"\x1b[1 qA")
        assert mtw.screen.buffer[0][0].data == "A"

    def test_getattr_delegates(self):
        mtw, inner = self._make_writer()
        inner.custom_attr = "test"
        assert mtw.custom_attr == "test"

    def test_font_switch_changes_encoding(self):
        mtw, inner = self._make_writer()
        assert mtw.encoding == "cp437"
        mtw.write(b"\x1b[0;24 D")
        assert mtw.font.font_id == 24
        assert mtw.encoding == "iso-8859-1"

    def test_font_switch_same_encoding_keeps_state(self):
        mtw, inner = self._make_writer()
        mtw.write(b"A")
        mtw.write(b"\x1b[0;26 D")
        assert mtw.font.font_id == 26
        assert mtw.encoding == "cp437"
        mtw.write(b"B")
        assert mtw.screen.buffer[0][0].data == "A"
        assert mtw.screen.buffer[0][1].data == "B"

    def test_dsr_sends_cpr_response(self):
        inner = FakeWriter()
        ctx = FakeCtx()
        fake_writer = FakeWriter()
        ctx.writer = fake_writer
        mtw = MetaTerminalWriter(inner, ctx, columns=80, rows=25)
        mtw.write(b"\x1b[6n")
        cpr = fake_writer.output
        assert cpr == "\x1b[1;1R"

    def test_dsr_stripped_from_pyte_input(self):
        mtw, inner = self._make_writer(columns=10, rows=3)
        mtw.write(b"\x1b[6nA")
        assert mtw.screen.buffer[0][0].data == "A"

    def test_full_redraw_after_font_switch(self):
        mtw, inner = self._make_writer(columns=5, rows=2)
        mtw.write(b"Hello")
        inner.clear()
        mtw.write(b"\x1b[0;42 DX")
        output = inner.output
        assert "\033[" in output

    def test_utf8_decode_reverses_raw_event_loop_encoding(self):
        """MetaTerminalWriter decodes stdin as UTF-8, not the connection encoding.

        telnetlib3's _raw_event_loop decodes wire bytes as the connection
        encoding (e.g. iso-8859-1) and re-encodes the str as UTF-8 via
        out.encode() before writing to stdout.  MetaTerminalWriter decodes
        as UTF-8 to recover the original Unicode string, preventing
        double-encoding that would split a single glyph into two.
        """
        mtw, inner = self._make_writer(columns=10, rows=3, encoding="iso-8859-1")
        inner.clear()
        mtw.write(b"\xc2\xa4")
        assert mtw.screen.buffer[0][0].data == "\u00a4"
        assert mtw.screen.buffer[0].get(1) is None
