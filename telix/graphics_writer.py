"""Graphics writer: pyte virtual terminal rendered via sixel/kitty graphics."""

import io
import re
import time
import asyncio
import logging

import pyte
import numpy as np

from . import terminal, session_context, graphics_renderer
from .fonts import font_registry
from .color_filter import PALETTES

log = logging.getLogger(__name__)

MIN_RENDER_INTERVAL = 0.033  # ~30 fps


class BBSScreen(pyte.Screen):
    """
    Pyte Screen subclass with BBS/CTerm compatibility adjustments.

    SyncTERM's CTerm (and most BBS software) treats ``ED 2`` (Erase in Display, mode 2) as clearing the screen AND
    moving the cursor home. The VT100/ECMA-48 spec says ``ED 2`` should not move the cursor, but virtually all BBS
    software depends on the home behavior.

    DECAWM (auto-wrap mode) is disabled because BBS software sends its own ``CR+LF`` line endings.  With DECAWM enabled,
    pyte inserts an extra ``CR+LF`` when text fills the rightmost column, doubling line spacing and causing wrapped-
    character artifacts.
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


SYNCTERM_FONT_RE = re.compile(r"\x1b\[(\d+);(\d+) D")

CSI_WITH_INTERMEDIATE = re.compile(r"\x1b\[[\d;]*[\x20-\x2f]+[\x40-\x7e]")

DECSCUSR_RE = re.compile(r"\x1b\[(\d) q")

XTGETTCAP_DCS_RE = re.compile(r"\x1bP\+q[^\x1b\x07]*(\x1b\\|\x07)")

SYNC_START = "\033[?2026h"
SYNC_END = "\033[?2026l"


def handle_cursor_shape(text: str) -> tuple[str, int | None, bool | None]:
    """
    Strip DECSCUSR sequences and return (text, shape, blink).

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


def intercept_device_queries(screen: pyte.Screen, ctx_writer, text: str) -> None:
    """Send CPR response if DSR is present in *text*."""
    if "\x1b[6n" not in text:
        return
    row = screen.cursor.y + 1
    col = screen.cursor.x + 1
    if ctx_writer is not None:
        ctx_writer.write(f"\x1b[{row};{col}R")


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
    for _i in range(24):
        v = 8 + 10 * _i
        palette.append((v, v, v))
    XTERM_256 = palette
    return XTERM_256


def pyte_color_to_rgb(
    color: str, bold: bool, is_fg: bool, palette: tuple[tuple[int, int, int], ...]
) -> tuple[int, int, int]:
    """Convert a pyte color value to an RGB tuple."""
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


class BitmapFont:
    """
    A variable-size bitmap font.

    :param font_id: Numeric font identifier.
    :param data: Raw glyph bytes (nglyphs * height bytes, one byte per row, MSB-left).
    :param name: Human-readable font name.
    :param encoding: Python codec name for the font's codepage.
    :param width: Glyph width in pixels.
    :param height: Glyph height in pixels.
    :param nglyphs: Number of glyphs in the font.
    """

    def __init__(
        self, font_id: int, data: bytes, name: str, encoding: str, width: int, height: int, nglyphs: int
    ) -> None:
        self.font_id = font_id
        self.data = data
        self.name = name
        self.encoding = encoding
        self.width = width
        self.height = height
        self.nglyphs = nglyphs

    def glyph(self, char_code: int) -> list[int]:
        """
        Return the bitmap rows for *char_code* (0..nglyphs-1).

        Each int is an 8-bit mask, MSB = leftmost pixel.
        """
        if char_code < 0 or char_code >= self.nglyphs:
            return [0] * self.height
        offset = char_code * self.height
        return list(self.data[offset : offset + self.height])


_font_cache: dict[int, BitmapFont] = {}
_font_bin_data: bytes | None = None
_font_by_id: dict[int, font_registry.FontEntry] = {e.font_id: e for e in font_registry.FONT_TABLE}

FONT_BIN_PATH = font_registry.FONT_BIN_PATH


def load_font_bin() -> bytes:
    global _font_bin_data
    if _font_bin_data is None:
        _font_bin_data = FONT_BIN_PATH.read_bytes()
    return _font_bin_data


def load_font(font_id: int) -> BitmapFont:
    """
    Load a font by ID, with caching.

    :raises KeyError: If *font_id* is not in the registry.
    """
    if font_id in _font_cache:
        return _font_cache[font_id]

    entry = _font_by_id[font_id]
    bin_data = load_font_bin()
    font_data = bin_data[entry.data_offset : entry.data_offset + entry.data_len]
    font = BitmapFont(font_id, font_data, entry.name, entry.encoding, entry.width, entry.height, entry.nglyphs)
    _font_cache[font_id] = font
    return font


class BaseScreenWriter:
    """
    Shared pyte virtual terminal pipeline.

    Parses server output, manages cursor state, and dispatches rendering to subclass hooks.

    :param inner: The underlying asyncio.StreamWriter (real stdout).
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
        self.font = load_font(font_id if font_id is not None else font_registry.DEFAULT_FONT_ID)
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

    def on_font_changed(self) -> None:
        """Called after a font switch sequence is processed."""

    def on_size_changed(self) -> None:
        """Called from ``_update_real_size`` when the real terminal size changes."""

    def on_resize(self) -> None:
        """Called from ``resize`` after updating virtual terminal dimensions."""

    def on_cursor_moved(self, prev_y: int, prev_x: int) -> None:
        """Called after dirty-set on the old cursor position."""

    def on_write_complete(self, cursor_moved: bool, shape_changed: bool) -> None:
        """Called at the end of ``write()`` to decide whether and how to render."""
        raise NotImplementedError

    def trigger_render(self) -> None:
        """Force a full render."""
        raise NotImplementedError

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

    def _handle_font_switch(self, text: str) -> str:
        """Strip and process SyncTERM font switching sequences."""

        def _on_match(m: re.Match) -> str:
            slot = int(m.group(1))
            font_id = int(m.group(2))
            if font_id in _font_by_id:
                new_font = load_font(font_id)
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

    def _update_real_size(self) -> bool:
        """
        Re-query the real terminal size.

        Return True if anything changed.
        """
        real_rows, real_cols = terminal.get_terminal_size()
        changed = real_rows != self._real_rows or real_cols != self._real_cols
        if not changed:
            return False
        self._real_rows = real_rows
        self._real_cols = real_cols
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
        self.on_resize()
        self._needs_full_redraw = True
        self.trigger_render()

    def write(self, data: bytes) -> None:
        """Decode, feed to pyte, and dispatch rendering."""
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


class GraphicsWriter(BaseScreenWriter):
    """
    Renders BBS output as sixel or kitty terminal graphics.

    Each virtual character cell is rasterized at the font's native pixel dimensions and transmitted as a terminal
    graphics frame. The terminal scales it to fit.

    :param inner: The underlying asyncio.StreamWriter (real stdout).
    :param ctx: Session context with graphics configuration.
    :param protocol: Graphics protocol to use ("kitty" or "sixel").
    :param encoding: Wire encoding override.
    :param columns: Virtual terminal columns (default 80).
    :param rows: Virtual terminal rows (default 25).
    :param cell_px_w: Width of a single character cell in pixels, or 0 for default.
    :param cell_px_h: Height of a single character cell in pixels, or 0 for default.
    :param font_id: Initial font id for bitmap rendering (default 0, IBM VGA).
    """

    def __init__(
        self,
        inner: asyncio.StreamWriter,
        ctx: session_context.TelixSessionContext,
        protocol: str,
        encoding: str | None = None,
        columns: int = 80,
        rows: int = 25,
        cell_px_w: int = 0,
        cell_px_h: int = 0,
        font_id: int | None = None,
    ) -> None:
        super().__init__(inner, ctx, encoding=encoding, columns=columns, rows=rows, font_id=font_id)
        self.protocol = protocol
        self._render_timer: asyncio.TimerHandle | None = None
        self._last_render_time = 0.0
        self._rendering = False
        self._did_initial_clear = False
        self._blink_timer: asyncio.TimerHandle | None = None
        self._cell_px_w = cell_px_w
        self._cell_px_h = cell_px_h
        self._glyph_cache: np.ndarray | None = None
        self._px_buf: np.ndarray | None = None

    def on_font_changed(self) -> None:
        self._glyph_cache = None

    def on_size_changed(self) -> None:
        self._px_buf = None
        self._glyph_cache = None
        self._did_initial_clear = False

    def on_resize(self) -> None:
        self._px_buf = None
        self._glyph_cache = None

    def on_write_complete(self, cursor_moved: bool, shape_changed: bool) -> None:
        self._manage_blink()
        if self.screen.dirty or self._needs_full_redraw or cursor_moved or shape_changed:
            self._schedule_render()

    def trigger_render(self) -> None:
        self._schedule_render()

    def cleanup(self) -> None:
        if self._render_timer is not None:
            self._render_timer.cancel()
            self._render_timer = None
        self._cancel_blink()
        super().cleanup()

    def _ensure_glyph_cache(self) -> None:
        if self._glyph_cache is not None:
            return
        fw = self.font.width
        fh = self.font.height
        ng = self.font.nglyphs
        cache = np.zeros((ng, fh, fw), dtype=bool)
        for cp in range(ng):
            rows = self.font.glyph(cp)
            for py in range(fh):
                bits = rows[py]
                for px in range(fw):
                    if (bits >> (fw - 1 - px)) & 1:
                        cache[cp, py, px] = True
        self._glyph_cache = cache

    def _build_pixel_buffers(self) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(bitmap, colors)`` float32 arrays for the current screen."""
        self._ensure_glyph_cache()
        cache = self._glyph_cache
        fw = self.font.width
        fh = self.font.height
        h = self.rows * fh
        w = self.columns * fw

        if self._px_buf is None or self._px_buf.shape != (h, w, 3):
            self._px_buf = np.zeros((h, w, 3), dtype=np.float32)
            self._needs_full_redraw = True
        colors = self._px_buf

        force = self._needs_full_redraw
        self._needs_full_redraw = False

        dirty_rows = set(range(self.rows)) if force else self.screen.dirty
        if dirty_rows:
            if force:
                colors.fill(0.0)

            fg_buf = np.empty(3, dtype=np.float32)
            bg_buf = np.empty(3, dtype=np.float32)

            for vrow in range(self.rows):
                if vrow not in dirty_rows:
                    continue
                row_data = self.screen.buffer.get(vrow, {})
                for vcol in range(self.columns):
                    char = row_data.get(vcol, self.screen.default_char)
                    fg = pyte_color_to_rgb(char.fg, char.bold, True, self.palette)
                    bg = pyte_color_to_rgb(char.bg, False, False, self.palette)
                    if char.reverse:
                        fg, bg = bg, fg
                    fg_buf[0] = fg[0] / 255.0
                    fg_buf[1] = fg[1] / 255.0
                    fg_buf[2] = fg[2] / 255.0
                    bg_buf[0] = bg[0] / 255.0
                    bg_buf[1] = bg[1] / 255.0
                    bg_buf[2] = bg[2] / 255.0

                    cp = self._char_to_code(char.data)
                    if 0 <= cp < self.font.nglyphs:
                        glyph = cache[cp]
                    else:
                        glyph = cache[0] if self.font.nglyphs > 0 else np.zeros((fh, fw), dtype=bool)
                    py = vrow * fh
                    px = vcol * fw
                    region = colors[py : py + fh, px : px + fw]
                    region[:] = bg_buf
                    region[glyph] = fg_buf

        self.screen.dirty.clear()
        self._draw_cursor(colors)
        bitmap = np.any(colors > 0.001, axis=2).astype(np.float32)
        return bitmap, colors

    def _draw_cursor(self, colors: np.ndarray) -> None:
        if self._cursor_blink:
            phase = int(time.monotonic() * 1000) % 1000
            if phase >= 500:
                return
        cx = self.screen.cursor.x
        cy = self.screen.cursor.y
        if not (0 <= cx < self.columns and 0 <= cy < self.rows):
            return
        fw = self.font.width
        fh = self.font.height
        py = cy * fh
        px = cx * fw
        shape = self._cursor_shape
        if shape in (0, 1, 2):
            y0, y1 = py, py + fh
            x0, x1 = px, px + fw
        elif shape in (3, 4):
            y0, y1 = py + fh - max(2, fh // 8), py + fh
            x0, x1 = px, px + fw
        else:
            y0, y1 = py, py + fh
            x0, x1 = px, px + max(2, fw // 4)
        h, w = colors.shape[:2]
        y0 = max(0, y0)
        y1 = min(h, y1)
        x0 = max(0, x0)
        x1 = min(w, x1)
        if y1 > y0 and x1 > x0:
            colors[y0:y1, x0:x1] = 1.0 - colors[y0:y1, x0:x1]

    def _render_full(self) -> None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            self._render_frame_sync()
            return
        loop.create_task(self._render_frame())

    def _render_frame_sync(self) -> None:
        self._rendering = True
        try:
            self._do_render()
        finally:
            self._rendering = False

    async def _render_frame(self) -> None:
        self._rendering = True
        try:
            self._do_render()
            await self.inner.drain()
        finally:
            self._rendering = False

    def _do_render(self) -> None:
        self._last_render_time = time.monotonic()
        self._render_timer = None

        if self._update_real_size():
            self._needs_full_redraw = True

        bitmap, colors = self._build_pixel_buffers()

        fw = self.font.width
        fh = self.font.height
        scale_w = max(1, (self._cell_px_w if self._cell_px_w > 0 else fw) // fw)
        scale_h = max(1, (self._cell_px_h if self._cell_px_h > 0 else fh) // fh)
        scale = min(scale_w, scale_h)
        if scale > 1:
            colors = np.repeat(np.repeat(colors, scale, axis=0), scale, axis=1)

        buf = io.StringIO()
        buf.write(SYNC_START)
        if not self._did_initial_clear or self._needs_full_redraw:
            buf.write("\033[H\033[2J")
            self._did_initial_clear = True

        buf.write("\033[H")

        if self.protocol == "kitty":
            graphics_renderer.encode_kitty(colors, buf, fmt="rgb", columns=self.columns, rows=self.rows)
        else:
            graphics_renderer.encode_sixel(colors, buf)
        buf.write(SYNC_END)

        self._output(buf.getvalue())

    def _schedule_render(self) -> None:
        if self._rendering:
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            self._render_full()
            return
        now = time.monotonic()
        elapsed = now - self._last_render_time
        if elapsed >= MIN_RENDER_INTERVAL:
            if self._render_timer is not None:
                self._render_timer.cancel()
                self._render_timer = None
            self._render_full()
        elif self._render_timer is None:
            delay = MIN_RENDER_INTERVAL - elapsed
            self._render_timer = loop.call_later(delay, self._render_full)

    def _manage_blink(self) -> None:
        should_blink = self._cursor_blink
        if should_blink and self._blink_timer is None:
            loop = asyncio.get_event_loop()
            self._blink_timer = loop.call_later(0.5, self._on_blink_tick)
        elif not should_blink and self._blink_timer is not None:
            self._blink_timer.cancel()
            self._blink_timer = None

    def _on_blink_tick(self) -> None:
        self._blink_timer = None
        self._schedule_render()
        self._manage_blink()

    def _cancel_blink(self) -> None:
        if self._blink_timer is not None:
            self._blink_timer.cancel()
            self._blink_timer = None
