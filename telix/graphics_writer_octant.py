"""Octant writer: pyte virtual terminal with Unicode octant block character rendering.

Each BBS character cell becomes a 4x4 block of real terminal cells.
Used in raw/BBS mode when a graphics font is enabled.
"""

import re
import time
import typing
import asyncio
import logging

import pyte

from . import graphics_bmpfont, terminal, session_context
from .fonts import font_registry
from .color_filter import PALETTES

log = logging.getLogger(__name__)


class BBSScreen(pyte.Screen):
    """pyte Screen subclass with BBS/CTerm compatibility adjustments.

    SyncTERM's CTerm (and most BBS software) treats ``ED 2`` (Erase in
    Display, mode 2) as clearing the screen AND moving the cursor home.
    The VT100/ECMA-48 spec says ``ED 2`` should not move the cursor, but
    virtually all BBS software depends on the home behavior.

    DECAWM (auto-wrap mode) is disabled because BBS software sends its
    own ``CR+LF`` line endings.  With DECAWM enabled, pyte inserts an
    extra ``CR+LF`` when text fills the rightmost column, doubling line
    spacing and causing wrapped-character artifacts.
    """

    def __init__(self, columns: int, lines: int) -> None:
        super().__init__(columns, lines)
        self.mode.discard(pyte.modes.DECAWM)

    def set_mode(self, *modes: int, **kwargs) -> None:
        super().set_mode(*modes, **kwargs)
        self.mode.discard(pyte.modes.DECAWM)

    def reset(self) -> None:
        super().reset()
        self.mode.discard(pyte.modes.DECAWM)

    def erase_in_display(self, how: int = 0, *args, **kwargs) -> None:
        super().erase_in_display(how, *args, **kwargs)
        if how == 2:
            self.cursor.x = 0
            self.cursor.y = 0


# SyncTERM font switching: ESC [ <slot> ; <font_id> SPACE D
# CSI with intermediate character ' ' and final character 'D'.
SYNCTERM_FONT_RE = re.compile(r"\x1b\[(\d+);(\d+) D")

# CSI sequences with intermediate bytes (0x20-0x2F) that pyte does not handle.
# pyte's parser treats these as standard CSI and passes extra params to handlers
# that don't expect them, causing crashes.  Strip them before feeding to pyte.
CSI_WITH_INTERMEDIATE = re.compile(r"\x1b\[[\d;]*[\x20-\x2f]+[\x40-\x7e]")

# DECSCUSR: CSI Ps SP q -- set cursor shape.
DECSCUSR_RE = re.compile(r"\x1b\[(\d) q")

# XTGETTCAP DCS sequences emitted by terminal query libraries.
# pyte does not handle DCS, so these render as visible garbage
# unless stripped before feeding pyte.
XTGETTCAP_DCS_RE = re.compile(r"\x1bP\+q[^\x1b\x07]*(\x1b\\|\x07)")


def handle_cursor_shape(text: str) -> tuple[str, int | None, bool | None]:
    """Strip DECSCUSR sequences and return (text, shape, blink).

    Returns None for shape/blink when no cursor shape sequence is found.
    """
    matches = list(DECSCUSR_RE.finditer(text))
    if not matches:
        return text, None, None
    val = int(matches[-1].group(1))
    text = DECSCUSR_RE.sub("", text)
    if val == 0:
        return text, 2, False
    return text, val, val in (1, 3, 5)


def intercept_device_queries(screen: pyte.Screen, ctx_writer: typing.Any, text: str) -> None:
    """Send CPR response if DSR is present in *text*."""
    if "\x1b[6n" not in text:
        return
    row = screen.cursor.y + 1
    col = screen.cursor.x + 1
    if ctx_writer is not None:
        ctx_writer.write(f"\x1b[{row};{col}R")


# Synchronized output: DEC private mode 2026.
SYNC_START = "\033[?2026h"
SYNC_END = "\033[?2026l"

# pyte uses named colors for the basic 16, hex strings for 256/24-bit.
PYTE_COLOR_NAMES: dict[str, int] = {
    "black": 0,
    "red": 1,
    "green": 2,
    "brown": 3,
    "blue": 4,
    "magenta": 5,
    "cyan": 6,
    "white": 7,
    "brightblack": 8,
    "brightred": 9,
    "brightgreen": 10,
    "brightyellow": 11,
    "brightblue": 12,
    "brightmagenta": 13,
    "brightcyan": 14,
    "brightwhite": 15,
}

# xterm 256-color palette: 16 standard + 216 color cube + 24 grayscale
XTERM_256: list[tuple[int, int, int]] | None = None


def build_xterm_256() -> list[tuple[int, int, int]]:
    global XTERM_256
    if XTERM_256 is not None:
        return XTERM_256
    palette: list[tuple[int, int, int]] = list(PALETTES["vga"])
    cube_values = [0, 95, 135, 175, 215, 255]
    for r in cube_values:
        for g in cube_values:
            for b in cube_values:
                palette.append((r, g, b))
    for i in range(24):
        v = 8 + 10 * i
        palette.append((v, v, v))
    XTERM_256 = palette
    return XTERM_256


def pyte_color_to_rgb(
    color: str, bold: bool, is_fg: bool, palette: tuple[tuple[int, int, int], ...]
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

    if color in PYTE_COLOR_NAMES:
        idx = PYTE_COLOR_NAMES[color]
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


class BaseScreenWriter:
    """Shared pyte virtual terminal pipeline.

    Parses server output, manages cursor state, and dispatches rendering to
    subclass hooks.  Subclasses implement ``on_write_complete`` to decide
    when and how to paint the screen.

    :param inner: The underlying ``asyncio.StreamWriter`` (real stdout).
    :param ctx: Session context with display configuration.
    :param encoding: Wire encoding override.
    :param columns: Virtual terminal columns (default 80).
    :param rows: Virtual terminal rows (default 25).
    :param font_id: Initial font id for bitmap rendering (default 0, IBM VGA).
    """

    def __init__(
        self,
        inner: asyncio.StreamWriter,
        ctx: session_context.TelixSessionContext,
        encoding: str | None = None,
        columns: int = 80,
        rows: int = 25,
        font_id: int | None = None,
    ) -> None:
        self.inner = inner
        self.ctx = ctx
        self.encoding = encoding or ctx.encoding or "utf-8"
        self.columns = columns
        self.rows = rows
        self.screen = BBSScreen(columns, rows)
        self.stream = pyte.Stream(self.screen)
        self.palette = PALETTES.get("vga", PALETTES["vga"])
        self.font = graphics_bmpfont.load_font(font_id if font_id is not None else font_registry.DEFAULT_FONT_ID)
        self._needs_full_redraw = True
        self._pending_resize: tuple[int, int] | None = None
        self._cursor_shape: int = 2
        self._cursor_blink: bool = False
        self._prev_cursor_x: int = 0
        self._prev_cursor_y: int = 0
        real_rows, real_cols = terminal.get_terminal_size()
        self._real_rows = real_rows
        self._real_cols = real_cols
        self._init_screen()

    # ------------------------------------------------------------------
    # hooks -- override in subclasses
    # ------------------------------------------------------------------

    def on_font_changed(self) -> None:
        """Called after a font switch sequence is processed."""

    def on_size_changed(self) -> None:
        """Called from ``_update_real_size`` when the real terminal size changes."""

    def on_resize(self) -> None:
        """Called from ``resize`` after updating virtual terminal dimensions."""

    def on_cursor_moved(self, prev_y: int, prev_x: int) -> None:
        """Called after dirty-set on the old cursor position.

        :param prev_y: Previous cursor row (already validated in-bounds).
        :param prev_x: Previous cursor column.
        """

    def on_write_complete(self, cursor_moved: bool, shape_changed: bool) -> None:
        """Called at the end of ``write()`` to decide whether and how to render.

        Subclasses must implement this hook.

        :param cursor_moved: True if the pyte cursor position changed.
        :param shape_changed: True if the cursor shape/blink changed.
        """
        raise NotImplementedError

    def trigger_render(self) -> None:
        """Force a full render.  Subclasses must implement this hook."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------

    def _output(self, data: str) -> None:
        self.inner.write(data.encode("utf-8", errors="replace"))

    def _init_screen(self) -> None:
        self._output("\033[?1049h\033[?25l\033[2J\033[H")

    def cleanup(self) -> None:
        self._output("\033[?25h\033[?1049l")

    def virtual_size(self) -> tuple[int, int]:
        return (self.rows, self.columns)

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    # ------------------------------------------------------------------
    # font switching
    # ------------------------------------------------------------------

    def _handle_font_switch(self, text: str) -> str:
        """Strip and process SyncTERM font switching sequences."""

        def _on_match(m: re.Match) -> str:
            slot = int(m.group(1))
            font_id = int(m.group(2))
            if font_id in font_registry.FONT_BY_ID:
                new_font = graphics_bmpfont.load_font(font_id)
                old_encoding = self.font.encoding
                self.font = new_font
                self._needs_full_redraw = True
                self.on_font_changed()
                if new_font.encoding != old_encoding:
                    self.encoding = new_font.encoding
                    log.debug(
                        "font switch: slot=%d font_id=%d (%s), encoding %s -> %s",
                        slot,
                        font_id,
                        new_font.name,
                        old_encoding,
                        new_font.encoding,
                    )
                else:
                    log.debug("font switch: slot=%d font_id=%d (%s)", slot, font_id, new_font.name)
            else:
                log.warning("unknown font id %d in slot %d", font_id, slot)
            return ""

        return SYNCTERM_FONT_RE.sub(_on_match, text)

    def _char_to_code(self, char_data: str) -> int:
        """Convert pyte character to font codepage byte value."""
        if not char_data or char_data == " ":
            return 0x20
        cp = ord(char_data)
        if cp < 0x80:
            return cp
        enc = self.font.encoding or "cp437"
        try:
            encoded = char_data.encode(enc, errors="replace")
            return encoded[0] if encoded else 0x3F
        except (LookupError, ValueError, TypeError):
            return 0x3F

    # ------------------------------------------------------------------
    # resize
    # ------------------------------------------------------------------

    def _update_real_size(self) -> bool:
        """Re-query the real terminal size.  Resize the virtual terminal
        if columns/rows are not forced.  Return True if anything changed."""
        real_rows, real_cols = terminal.get_terminal_size()
        changed = real_rows != self._real_rows or real_cols != self._real_cols
        if not changed:
            return False
        self._real_rows = real_rows
        self._real_cols = real_cols
        new_cols = self.ctx.repl.graphics_columns
        new_rows = self.ctx.repl.graphics_rows
        if new_cols is None:
            new_cols = real_cols // graphics_bmpfont.CELLS_PER_CHAR_X
        if new_rows is None:
            new_rows = real_rows // graphics_bmpfont.CELLS_PER_CHAR_Y
        if new_cols >= 1 and new_rows >= 1 and (new_cols != self.columns or new_rows != self.rows):
            self.columns = new_cols
            self.rows = new_rows
            self.screen.resize(new_rows, new_cols)
        self.on_size_changed()
        return True

    def schedule_resize(self, real_cols: int, real_rows: int) -> None:
        self._pending_resize = (real_cols, real_rows)

    def _apply_pending_resize(self) -> None:
        pending = self._pending_resize
        if pending is None:
            return
        self._pending_resize = None
        self.resize(*pending)

    def resize(self, real_cols: int, real_rows: int) -> None:
        self._real_cols = real_cols
        self._real_rows = real_rows
        new_cols = self.ctx.repl.graphics_columns
        new_rows = self.ctx.repl.graphics_rows
        if new_cols is None:
            new_cols = real_cols // graphics_bmpfont.CELLS_PER_CHAR_X
        if new_rows is None:
            new_rows = real_rows // graphics_bmpfont.CELLS_PER_CHAR_Y
        if new_cols < 1 or new_rows < 1:
            return
        if new_cols != self.columns or new_rows != self.rows:
            self.columns = new_cols
            self.rows = new_rows
            self.screen.resize(new_rows, new_cols)
        self.on_resize()
        self._needs_full_redraw = True
        self.trigger_render()

    # ------------------------------------------------------------------
    # write pipeline
    # ------------------------------------------------------------------

    def write(self, data: bytes) -> None:
        """Decode, feed to pyte, and dispatch rendering.

        *data* arrives via telnetlib3's ``_raw_event_loop`` which decodes
        wire bytes using the connection encoding and then re-encodes the
        resulting ``str`` as UTF-8 with ``out.encode()`` before writing to
        ``stdout``.  We decode as UTF-8 to recover the original Unicode
        string, undoing that re-encoding step.
        """
        from . import client_shell

        if self.ctx.repl.ff_clears_screen:
            data = client_shell.replace_ff_with_clear(data)
        if self.ctx.repl.clear_homes_cursor:
            data = client_shell.inject_home_before_clear(data)

        self._apply_pending_resize()

        text = data.decode("utf-8", errors="replace")
        text = self._handle_font_switch(text)
        prev_shape = self._cursor_shape
        prev_blink = self._cursor_blink
        text, shape, blink = handle_cursor_shape(text)
        if shape is not None:
            self._cursor_shape = shape
            self._cursor_blink = blink
        shape_changed = self._cursor_shape != prev_shape or self._cursor_blink != prev_blink
        cf = getattr(self.ctx.repl, "color_filter", None)
        if cf is not None:
            text = cf.filter(text)
        text = CSI_WITH_INTERMEDIATE.sub("", text)
        text = XTGETTCAP_DCS_RE.sub("", text)

        if text:
            self.stream.feed(text)

        intercept_device_queries(self.screen, self.ctx.writer, text)

        cursor_moved = self.screen.cursor.x != self._prev_cursor_x or self.screen.cursor.y != self._prev_cursor_y
        if cursor_moved and 0 <= self._prev_cursor_y < self.rows:
            self.screen.dirty.add(self._prev_cursor_y)
            self.on_cursor_moved(self._prev_cursor_y, self._prev_cursor_x)
        self._prev_cursor_x = self.screen.cursor.x
        self._prev_cursor_y = self.screen.cursor.y

        self.on_write_complete(cursor_moved, shape_changed)


class OctantWriter(BaseScreenWriter):
    """Renders BBS output as Unicode octant block characters.

    Each virtual character cell occupies ``CELLS_PER_CHAR_X`` by
    ``CELLS_PER_CHAR_Y`` real terminal cells, drawn with Unicode octant
    glyphs (U+2580-U+259F block elements).

    :param inner: The underlying ``asyncio.StreamWriter`` (real stdout).
    :param ctx: Session context with graphics font configuration.
    :param encoding: Wire encoding override.
    :param columns: Virtual terminal columns (default 80).
    :param rows: Virtual terminal rows (default 25).
    :param font_id: Initial font id for bitmap rendering (default 0, IBM VGA).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._prev_buffer: dict[tuple[int, int], tuple[str, str, str, bool, bool]] = {}
        self._cursor_hidden: bool = False

    def on_cursor_moved(self, prev_y: int, prev_x: int) -> None:
        self._prev_buffer.pop((prev_y, prev_x), None)

    def on_write_complete(self, cursor_moved: bool, shape_changed: bool) -> None:
        prev_hidden = self._cursor_hidden
        self._cursor_hidden = self.screen.cursor.hidden
        if self.screen.dirty or self._needs_full_redraw or self._cursor_hidden != prev_hidden or cursor_moved:
            self._render_dirty()

    def on_resize(self) -> None:
        self._prev_buffer.clear()

    def trigger_render(self) -> None:
        self._render_dirty()

    def _apply_pending_resize_and_redraw(self) -> None:
        self._apply_pending_resize()
        if self._update_real_size():
            self._prev_buffer.clear()
            self._needs_full_redraw = True
        if self._needs_full_redraw:
            self._render_dirty()

    def _render_dirty(self) -> None:
        """Diff the pyte screen against previous state and render changed cells."""
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
            real_row_base = vrow * graphics_bmpfont.CELLS_PER_CHAR_Y + 1
            if real_row_base > max_real_row:
                continue
            row_data = self.screen.buffer.get(vrow, {})
            for vcol in range(self.columns):
                real_col = vcol * graphics_bmpfont.CELLS_PER_CHAR_X + 1
                if real_col + graphics_bmpfont.CELLS_PER_CHAR_X - 1 > max_real_col:
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
                lines = graphics_bmpfont.render_cell(char_code, fg, bg, self.font)

                for i, line in enumerate(lines):
                    row_pos = real_row_base + i
                    if row_pos > max_real_row:
                        break
                    buf.append(f"\033[{row_pos};{real_col}H{line}")

        buf.append("\033[0m")

        if not self._cursor_hidden:
            do_cursor = not self._cursor_blink or int(time.monotonic() * 1000) % 1000 < 500
            if do_cursor:
                cx = self.screen.cursor.x
                cy = self.screen.cursor.y
                if 0 <= cx < self.columns and 0 <= cy < self.rows:
                    cur_base = cy * graphics_bmpfont.CELLS_PER_CHAR_Y + 1
                    cur_col = cx * graphics_bmpfont.CELLS_PER_CHAR_X + 1
                    row_data = self.screen.buffer.get(cy, {})
                    char = row_data.get(cx, self.screen.default_char)
                    fg = pyte_color_to_rgb(char.fg, char.bold, True, self.palette)
                    bg = pyte_color_to_rgb(char.bg, False, False, self.palette)
                    if char.reverse:
                        fg, bg = bg, fg
                    char_code = self._char_to_code(char.data)
                    lines = graphics_bmpfont.render_cell(char_code, fg, bg, self.font)
                    for i, line in enumerate(lines):
                        row_pos = cur_base + i
                        if row_pos > max_real_row:
                            break
                        block = self._cursor_shape in (0, 1, 2)
                        uline = self._cursor_shape in (3, 4) and i == graphics_bmpfont.CELLS_PER_CHAR_Y - 1
                        if block or uline:
                            buf.append(f"\033[{row_pos};{cur_col}H\033[7m{line}\033[27m")
                        elif self._cursor_shape in (5, 6):
                            buf.append(f"\033[{row_pos};{cur_col}H\033[7m{line[0]}\033[27m{line[1:]}")
                        else:
                            buf.append(f"\033[{row_pos};{cur_col}H{line}")

        buf.append(SYNC_END)

        if len(buf) > 3:
            self._output("".join(buf))

        self.screen.dirty.clear()
