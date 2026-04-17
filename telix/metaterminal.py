"""Meta terminal: pyte virtual terminal with octant bitmap font rendering.

Intercepts BBS output, feeds it through a pyte virtual terminal emulator,
and re-renders the screen using bitmap font glyphs encoded as Unicode octant
block characters.  Each BBS character cell becomes a 4x4 block of real
terminal cells.

Used in raw/BBS mode when the ``metafont`` option is enabled.
"""

import re
import codecs
import asyncio
import logging

import pyte

from . import metafont, terminal, session_context
from .color_filter import PALETTES
from .fonts import font_registry

log = logging.getLogger(__name__)


class BBSScreen(pyte.Screen):
    """pyte Screen subclass with BBS/CTerm compatibility adjustments.

    SyncTERM's CTerm (and most BBS software) treats ``ED 2`` (Erase in
    Display, mode 2) as clearing the screen AND moving the cursor home.
    The VT100/ECMA-48 spec says ``ED 2`` should not move the cursor, but
    virtually all BBS software depends on the home behavior.
    """

    def erase_in_display(self, how: int = 0, *args, **kwargs) -> None:
        super().erase_in_display(how, *args, **kwargs)
        if how == 2:
            self.cursor.x = 0
            self.cursor.y = 0

# SyncTERM font switching: ESC [ <slot> ; <font_id> SPACE D
# CSI with intermediate character ' ' and final character 'D'.
SYNCTERM_FONT_RE = re.compile(r"\x1b\[(\d+);(\d+) D")

# Device Status Report (DSR): CSI 6 n -- requests cursor position report.
# BBS systems send this to detect ANSI support; pyte has no output transport
# to reply, so we intercept it and send the response ourselves.
DSR_RE = re.compile(r"\x1b\[6n")

# CSI sequences with intermediate bytes (0x20-0x2F) that pyte does not handle.
# pyte's parser treats these as standard CSI and passes extra params to handlers
# that don't expect them, causing crashes.  Strip them before feeding to pyte.
CSI_WITH_INTERMEDIATE = re.compile(r"\x1b\[[\d;]*[\x20-\x2f]+[\x40-\x7e]")

# Synchronized output: DEC private mode 2026.
SYNC_START = "\033[?2026h"
SYNC_END = "\033[?2026l"

# pyte uses named colors for the basic 16, hex strings for 256/24-bit.
_PYTE_COLOR_NAMES: dict[str, int] = {
    "black": 0, "red": 1, "green": 2, "brown": 3,
    "blue": 4, "magenta": 5, "cyan": 6, "white": 7,
    "brightblack": 8, "brightred": 9, "brightgreen": 10, "brightyellow": 11,
    "brightblue": 12, "brightmagenta": 13, "brightcyan": 14, "brightwhite": 15,
}

# xterm 256-color palette: 16 standard + 216 color cube + 24 grayscale
_XTERM_256: list[tuple[int, int, int]] | None = None


def build_xterm_256() -> list[tuple[int, int, int]]:
    global _XTERM_256
    if _XTERM_256 is not None:
        return _XTERM_256
    palette: list[tuple[int, int, int]] = list(PALETTES["vga"])
    cube_values = [0, 95, 135, 175, 215, 255]
    for r in cube_values:
        for g in cube_values:
            for b in cube_values:
                palette.append((r, g, b))
    for i in range(24):
        v = 8 + 10 * i
        palette.append((v, v, v))
    _XTERM_256 = palette
    return _XTERM_256


def pyte_color_to_rgb(
    color: str,
    bold: bool,
    is_fg: bool,
    palette: tuple[tuple[int, int, int], ...],
) -> tuple[int, int, int]:
    """Convert a pyte color value to an RGB tuple.

    :param color: pyte color string ('default', named, 6-hex, or 256-color index).
    :param bold: Whether the character has bold attribute (brightens fg colors 0-7).
    :param is_fg: True for foreground, False for background.
    :param palette: 16-color palette to use for standard colors.
    :returns: RGB tuple.
    """
    if color == "default":
        return palette[7] if is_fg else palette[0]

    if color in _PYTE_COLOR_NAMES:
        idx = _PYTE_COLOR_NAMES[color]
        if bold and is_fg and idx < 8:
            idx += 8
        return palette[idx] if idx < len(palette) else (170, 170, 170)

    if len(color) == 6:
        try:
            return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))
        except ValueError:
            pass

    try:
        idx = int(color)
        xterm = build_xterm_256()
        if 0 <= idx < len(xterm):
            return xterm[idx]
    except ValueError:
        pass

    return palette[7] if is_fg else palette[0]


class MetaTerminalWriter:
    """Wraps stdout to render BBS output through a pyte virtual terminal using octant metafonts.

    Drop-in replacement for :class:`~telix.client_shell.ColorFilteredWriter` in the
    raw-mode data path.

    :param inner: The underlying ``asyncio.StreamWriter`` (real stdout).
    :param ctx: Session context with metafont configuration.
    :param encoding: Wire encoding override.
    :param columns: Virtual terminal columns (default 80).
    :param rows: Virtual terminal rows (default 25).
    """

    def __init__(
        self,
        inner: asyncio.StreamWriter,
        ctx: session_context.TelixSessionContext,
        encoding: str | None = None,
        columns: int = 80,
        rows: int = 25,
    ) -> None:
        self.inner = inner
        self.ctx = ctx
        self.encoding = encoding or ctx.encoding or "utf-8"
        self._decoder: codecs.IncrementalDecoder | None = None
        self.columns = columns
        self.rows = rows
        self.screen = BBSScreen(columns, rows)
        self.stream = pyte.Stream(self.screen)
        self.palette = PALETTES.get("vga", PALETTES["vga"])
        self.font = metafont.load_font(font_registry.DEFAULT_FONT_ID)
        self._prev_buffer: dict[tuple[int, int], tuple[str, str, str, bool, bool]] = {}
        self._needs_full_redraw = True
        self._pending_resize: tuple[int, int] | None = None
        real_rows, real_cols = terminal.get_terminal_size()
        self._real_rows = real_rows
        self._real_cols = real_cols
        self._init_screen()

    def _output(self, data: str) -> None:
        """Write a string to the real terminal."""
        self.inner.write(data.encode("utf-8", errors="replace"))

    def _init_screen(self) -> None:
        """Switch to alternate screen, hide cursor, clear, prepare for rendering."""
        self._output("\033[?1049h\033[?25l\033[2J\033[H")

    def cleanup(self) -> None:
        """Restore main screen and show cursor."""
        self._output("\033[?25h\033[?1049l")

    def _handle_font_switch(self, text: str) -> str:
        """Strip and process SyncTERM font switching sequences.

        :param text: Input text that may contain font switch sequences.
        :returns: Text with font switch sequences removed.
        """
        def _on_match(m: re.Match) -> str:
            slot = int(m.group(1))
            font_id = int(m.group(2))
            if font_id in font_registry.FONT_BY_ID:
                new_font = metafont.load_font(font_id)
                old_encoding = self.font.encoding
                self.font = new_font
                self._needs_full_redraw = True
                if new_font.encoding != old_encoding:
                    self.encoding = new_font.encoding
                    self._decoder = None
                    log.debug(
                        "font switch: slot=%d font_id=%d (%s), encoding %s -> %s",
                        slot, font_id, new_font.name, old_encoding, new_font.encoding,
                    )
                else:
                    log.debug("font switch: slot=%d font_id=%d (%s)", slot, font_id, new_font.name)
            else:
                log.warning("unknown font id %d in slot %d", font_id, slot)
            return ""
        return SYNCTERM_FONT_RE.sub(_on_match, text)

    def _intercept_device_queries(self, text: str) -> str:
        """Strip DSR (Device Status Report) and send CPR back to the BBS.

        BBS systems send ``CSI 6 n`` to detect ANSI support.  pyte has no
        output transport to reply, so we intercept it here and write the
        Cursor Position Report (``CSI row ; col R``) to the telnet writer.

        :param text: Input text that may contain DSR sequences.
        :returns: Text with DSR sequences removed.
        """
        if "\x1b[6n" not in text:
            return text
        row = self.screen.cursor.y + 1
        col = self.screen.cursor.x + 1
        writer = self.ctx.writer
        if writer is not None:
            cpr = f"\x1b[{row};{col}R".encode("ascii")
            writer.write(cpr)
        return DSR_RE.sub("", text)

    def _char_to_code(self, char_data: str) -> int:
        """Convert a pyte character to a font codepage byte value.

        pyte stores characters as Unicode.  For codepage fonts (CP437, etc.),
        we encode back to the font's codepage to get the byte value that
        indexes into the font bitmap.  Characters that cannot be encoded
        are mapped to 0x3F ('?') -- the font's own question mark glyph.

        :param char_data: Single character from pyte screen buffer.
        :returns: Byte value (0-255) in the font's encoding.
        """
        if not char_data or char_data == " ":
            return 0x20
        cp = ord(char_data)
        if cp < 0x80:
            return cp
        try:
            encoded = char_data.encode(self.font.encoding, errors="replace")
            return encoded[0] if encoded else 0x3F
        except LookupError:
            return 0x3F

    def _update_real_size(self) -> bool:
        """Re-query the real terminal size.  Resize the virtual terminal
        if columns/rows are not forced.  Return True if anything changed."""
        real_rows, real_cols = terminal.get_terminal_size()
        changed = (real_rows != self._real_rows or real_cols != self._real_cols)
        if not changed:
            return False
        self._real_rows = real_rows
        self._real_cols = real_cols
        new_cols = self.ctx.repl.metafont_columns
        new_rows = self.ctx.repl.metafont_rows
        if new_cols is None:
            new_cols = real_cols // metafont.CELLS_PER_CHAR_X
        if new_rows is None:
            new_rows = real_rows // metafont.CELLS_PER_CHAR_Y
        if new_cols >= 1 and new_rows >= 1 and (new_cols != self.columns or new_rows != self.rows):
            self.columns = new_cols
            self.rows = new_rows
            self.screen.resize(new_rows, new_cols)
        return True

    def _render_dirty(self) -> None:
        """Diff the pyte screen against previous state and render changed cells.

        Only renders cells whose real-terminal position fits within the
        current real terminal dimensions, preventing scrolling corruption.
        """
        if self._update_real_size():
            self._prev_buffer.clear()
            self._needs_full_redraw = True

        buf: list[str] = [SYNC_START]
        force = self._needs_full_redraw
        self._needs_full_redraw = False
        dirty_rows = set(range(self.rows)) if force else self.screen.dirty

        if force:
            buf.append("\033[2J")

        max_real_row = self._real_rows
        max_real_col = self._real_cols

        for vrow in dirty_rows:
            real_row_base = vrow * metafont.CELLS_PER_CHAR_Y + 1
            if real_row_base > max_real_row:
                continue
            row_data = self.screen.buffer.get(vrow, {})
            for vcol in range(self.columns):
                real_col = vcol * metafont.CELLS_PER_CHAR_X + 1
                if real_col + metafont.CELLS_PER_CHAR_X - 1 > max_real_col:
                    continue
                char = row_data.get(vcol, self.screen.default_char)
                key = (char.data, char.fg, char.bg, char.bold, char.blink)
                prev = self._prev_buffer.get((vrow, vcol))
                if not force and prev == key:
                    continue
                self._prev_buffer[(vrow, vcol)] = key

                fg = pyte_color_to_rgb(char.fg, char.bold, True, self.palette)
                bg = pyte_color_to_rgb(char.bg, False, False, self.palette)
                if char.reverse:
                    fg, bg = bg, fg

                char_code = self._char_to_code(char.data)
                lines = metafont.render_cell(char_code, fg, bg, self.font)

                for i, line in enumerate(lines):
                    row_pos = real_row_base + i
                    if row_pos > max_real_row:
                        break
                    buf.append(f"\033[{row_pos};{real_col}H{line}")

        buf.append("\033[0m")
        buf.append(SYNC_END)

        if len(buf) > 3:
            self._output("".join(buf))

        self.screen.dirty.clear()

    def schedule_resize(self, real_cols: int, real_rows: int) -> None:
        """Record a pending resize to be applied on the next write.

        Safe to call from a signal handler -- does no I/O.

        :param real_cols: New real terminal width.
        :param real_rows: New real terminal height.
        """
        self._pending_resize = (real_cols, real_rows)

    def _apply_pending_resize(self) -> None:
        """Apply a pending resize if one was scheduled."""
        pending = self._pending_resize
        if pending is None:
            return
        self._pending_resize = None
        self.resize(*pending)

    def _apply_pending_resize_and_redraw(self) -> None:
        """Apply pending resize and force a full redraw.

        Called via ``loop.call_soon()`` from the SIGWINCH handler so that
        the redraw happens on the event loop, not in the signal handler.
        """
        self._apply_pending_resize()
        if self._update_real_size():
            self._prev_buffer.clear()
            self._needs_full_redraw = True
        if self._needs_full_redraw:
            self._render_dirty()

    def write(self, data: bytes) -> None:
        """Decode, feed to pyte, and render changes.

        :param data: Raw bytes from the remote BBS connection.
        """
        from . import client_shell

        if self.ctx.repl.ff_clears_screen:
            data = client_shell.replace_ff_with_clear(data)
        if self.ctx.repl.clear_homes_cursor:
            data = client_shell.inject_home_before_clear(data)

        self._apply_pending_resize()

        cur_encoding = self.encoding
        if self._decoder is None or cur_encoding != getattr(self._decoder, "_encoding", ""):
            self._decoder = codecs.getincrementaldecoder(cur_encoding)(errors="replace")
            self._decoder._encoding = cur_encoding  # type: ignore[attr-defined]

        text = self._decoder.decode(data)
        text = self._intercept_device_queries(text)
        text = self._handle_font_switch(text)
        text = CSI_WITH_INTERMEDIATE.sub("", text)

        if text:
            self.stream.feed(text)
            self._render_dirty()

    def resize(self, real_cols: int, real_rows: int) -> None:
        """Resize the virtual terminal to fit the real terminal dimensions.

        When forced columns/rows are set, only the non-forced dimension
        changes.  Updates real terminal bounds and triggers full redraw.

        :param real_cols: Real terminal width in columns.
        :param real_rows: Real terminal height in rows.
        """
        self._real_cols = real_cols
        self._real_rows = real_rows
        new_cols = self.ctx.repl.metafont_columns
        new_rows = self.ctx.repl.metafont_rows
        if new_cols is None:
            new_cols = real_cols // metafont.CELLS_PER_CHAR_X
        if new_rows is None:
            new_rows = real_rows // metafont.CELLS_PER_CHAR_Y
        if new_cols < 1 or new_rows < 1:
            return
        if new_cols != self.columns or new_rows != self.rows:
            self.columns = new_cols
            self.rows = new_rows
            self.screen.resize(new_rows, new_cols)
        self._prev_buffer.clear()
        self._needs_full_redraw = True
        self._render_dirty()

    def virtual_size(self) -> tuple[int, int]:
        """Return the virtual terminal size (rows, columns) for NAWS reporting."""
        return (self.rows, self.columns)

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)
