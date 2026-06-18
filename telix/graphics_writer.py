"""Graphics writer: pyte virtual terminal rendered via sixel/kitty graphics.

Feeds BBS output through a :class:`pyte.Screen` and re-renders the full
virtual terminal as a pixel image using sixel or kitty graphics escape
sequences.  Each BBS character cell is rendered using the bitmap font
glyph, producing a 640x400 pixel image for an 80x25 virtual terminal.
"""

import io
import re
import asyncio
import logging
import time

import pyte
import numpy as np

from . import metafont, terminal, session_context, graphics_renderer
from .fonts import font_registry
from .color_filter import PALETTES
from .metaterminal import (
    DSR_RE,
    SYNC_END,
    SYNC_START,
    SYNCTERM_FONT_RE,
    CSI_WITH_INTERMEDIATE,
    BBSScreen,
    pyte_color_to_rgb,
)

log = logging.getLogger(__name__)

# DECSCUSR: CSI Ps SP q -- set cursor shape (xterm extension).
# Ps: 0=default, 1=blink block, 2=steady block, 3=blink underline,
# 4=steady underline, 5=blink bar, 6=steady bar.
DECSCUSR_RE = re.compile(r"\x1b\[(\d) q")

# XTGETTCAP DCS sequences emitted by terminal query libraries
# (blessed, ncurses).  pyte does not handle DCS, so these render
# as visible garbage unless stripped before feeding pyte.
# Matches: DCS +q <hex> ST  (7-bit form: \x1bP+q...\x1b\\)
XTGETTCAP_DCS_RE = re.compile(r"\x1bP\+q[^\x1b\x07]*(\x1b\\|\x07)")

MIN_RENDER_INTERVAL = 0.033  # ~30 fps

FONT_CELL_W = 8
FONT_CELL_H = 16


class GraphicsWriter:
    """Wraps stdout to render BBS output as sixel or kitty graphics.

    Drop-in replacement for :class:`~telix.metaterminal.MetaTerminalWriter`.

    :param inner: The underlying ``asyncio.StreamWriter`` (real stdout).
    :param ctx: Session context with graphics configuration.
    :param protocol: Graphics protocol to use (``"kitty"`` or ``"sixel"``).
    :param encoding: Wire encoding override.
    :param columns: Virtual terminal columns (default 80).
    :param rows: Virtual terminal rows (default 25).
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
        self.inner = inner
        self.ctx = ctx
        self.protocol = protocol
        self.encoding = encoding or ctx.encoding or "utf-8"
        self.columns = columns
        self.rows = rows
        self.screen = BBSScreen(columns, rows)
        self.stream = pyte.Stream(self.screen)
        self.palette = PALETTES.get("vga", PALETTES["vga"])
        self.font = metafont.load_font(font_id if font_id is not None else font_registry.DEFAULT_FONT_ID)
        self._needs_full_redraw = True
        self._render_scheduled = False
        self._render_timer: asyncio.TimerHandle | None = None
        self._last_render_time = 0.0
        self._rendering = False
        self._did_initial_clear = False
        self._need_px_query = False
        self._cursor_shape: int = 2  # steady block, DECSCUSR default
        self._cursor_blink: bool = False
        self._cursor_hidden: bool = False
        self._prev_cursor_x: int = 0
        self._prev_cursor_y: int = 0
        self._blink_timer: asyncio.TimerHandle | None = None
        self._pending_resize: tuple[int, int] | None = None
        real_rows, real_cols = terminal.get_terminal_size()
        self._real_rows = real_rows
        self._real_cols = real_cols
        self._image_w = columns * FONT_CELL_W
        self._image_h = rows * FONT_CELL_H
        self._cell_px_w = cell_px_w
        self._cell_px_h = cell_px_h
        self._glyph_cache: np.ndarray | None = None
        # Pre-allocated pixel buffers reused across frames.
        self._px_buf: np.ndarray | None = None
        self._init_screen()

    def _output(self, data: str) -> None:
        """Write short control sequences via the asyncio transport."""
        self.inner.write(data.encode("utf-8"))

    def _init_screen(self) -> None:
        """Switch to alternate screen, hide cursor, clear, home."""
        self._output("\033[?1049h\033[?25l\033[2J\033[H")

    def cleanup(self) -> None:
        """Restore main screen, cancel pending render, show cursor."""
        if self._render_timer is not None:
            self._render_timer.cancel()
            self._render_timer = None
        self._cancel_blink()
        self._output("\033[?25h\033[?1049l")

    def _handle_font_switch(self, text: str) -> str:
        """Strip and process SyncTERM font switching sequences."""

        def _on_match(m: re.Match) -> str:
            slot = int(m.group(1))
            font_id = int(m.group(2))
            if font_id in font_registry.FONT_BY_ID:
                new_font = metafont.load_font(font_id)
                old_encoding = self.font.encoding
                self.font = new_font
                self._glyph_cache = None
                self._needs_full_redraw = True
                if new_font.encoding != old_encoding:
                    self.encoding = new_font.encoding
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

    def _handle_cursor_shape(self, text: str) -> str:
        """Strip and process DECSCUSR cursor shape sequences.

        Must be called before ``CSI_WITH_INTERMEDIATE`` strips them.
        Processes all occurrences in *text*; the last one wins
        (later sequences override earlier ones in the same chunk).
        """
        matches = list(DECSCUSR_RE.finditer(text))
        if matches:
            # Use the LAST match (most recent override).
            val = int(matches[-1].group(1))
            if val == 0:
                self._cursor_shape = 2
                self._cursor_blink = False
            else:
                self._cursor_shape = val
                self._cursor_blink = val in (1, 3, 5)
        return DECSCUSR_RE.sub("", text)

    def _intercept_device_queries(self, text: str) -> str:
        """Strip DSR and send CPR back to the BBS."""
        if "\x1b[6n" not in text:
            return text
        row = self.screen.cursor.y + 1
        col = self.screen.cursor.x + 1
        writer = self.ctx.writer
        if writer is not None:
            cpr = f"\x1b[{row};{col}R"
            writer.write(cpr)
        return DSR_RE.sub("", text)

    def _char_to_code(self, char_data: str) -> int:
        """Convert a pyte character to a font codepage byte value."""
        if not char_data or char_data == " ":
            return 0x20
        cp = ord(char_data)
        if cp < 0x80:
            return cp
        enc = getattr(self.font, "encoding", None) or "cp437"
        try:
            encoded = char_data.encode(enc, errors="replace")
            return encoded[0] if encoded else 0x3F
        except (LookupError, ValueError, TypeError):
            return 0x3F

    def _ensure_glyph_cache(self) -> None:
        """Precompute glyph pixel masks for all 256 codepoints.

        Builds a ``(256, GLYPH_H, GLYPH_W)`` bool array.  Rebuilt lazily
        when the font changes (``_glyph_cache`` is set to ``None``).
        """
        if self._glyph_cache is not None:
            return
        cache = np.zeros((256, FONT_CELL_H, FONT_CELL_W), dtype=bool)
        for cp in range(256):
            rows = self.font.glyph(cp)
            for py in range(FONT_CELL_H):
                bits = rows[py]
                for px in range(FONT_CELL_W):
                    if (bits >> (7 - px)) & 1:
                        cache[cp, py, px] = True
        self._glyph_cache = cache

    def _build_pixel_buffers(self) -> tuple[np.ndarray, np.ndarray]:
        """Render pyte screen to pixel arrays using precomputed glyph masks.

        On the first call or after a full-redraw request, builds the
        entire image cell by cell.  Otherwise only updates pixel regions
        for rows marked dirty by pyte.

        :returns: ``(bitmap, colors)`` where *colors* is ``(H, W, 3)`` float32.
        """
        self._ensure_glyph_cache()
        cache = self._glyph_cache
        h = self.rows * FONT_CELL_H
        w = self.columns * FONT_CELL_W

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
                    cp = max(0, min(255, cp))  # clamp to valid glyph index
                    glyph = cache[cp]
                    py = vrow * FONT_CELL_H
                    px = vcol * FONT_CELL_W
                    region = colors[py : py + FONT_CELL_H, px : px + FONT_CELL_W]
                    region[:] = bg_buf
                    region[glyph] = fg_buf

        self.screen.dirty.clear()
        self._draw_cursor(colors)
        bitmap = np.any(colors > 0.001, axis=2).astype(np.float32)
        return bitmap, colors

    def _draw_cursor(self, colors: np.ndarray) -> None:
        """Draw the cursor on the pixel buffer using inverse video.

        Reads cursor shape, blink, and hidden state.  Blinking cursors
        toggle visibility every 500 ms (2 Hz phase).  The cursor region
        is drawn by inverting pixel values (``1.0 - value``), producing
        the classic reverse-video effect.

        :param colors: ``(H, W, 3)`` float32 pixel array, modified in-place.
        """
        if self._cursor_blink:
            phase = int(time.monotonic() * 1000) % 1000
            if phase >= 500:
                log.debug("blink hidden phase")
                return
            log.debug("blink visible phase, shape=%d", self._cursor_shape)
        cx = self.screen.cursor.x
        cy = self.screen.cursor.y
        if not (0 <= cx < self.columns and 0 <= cy < self.rows):
            return
        py = cy * FONT_CELL_H
        px = cx * FONT_CELL_W
        shape = self._cursor_shape
        if shape in (0, 1, 2):  # block
            y0, y1 = py, py + FONT_CELL_H
            x0, x1 = px, px + FONT_CELL_W
        elif shape in (3, 4):  # underline (bottom 2 pixel rows)
            y0, y1 = py + FONT_CELL_H - 2, py + FONT_CELL_H
            x0, x1 = px, px + FONT_CELL_W
        else:  # bar (left 2 pixel columns)
            y0, y1 = py, py + FONT_CELL_H
            x0, x1 = px, px + 2
        h, w = colors.shape[:2]
        y0 = max(0, y0); y1 = min(h, y1)
        x0 = max(0, x0); x1 = min(w, x1)
        if y1 > y0 and x1 > x0:
            colors[y0:y1, x0:x1] = 1.0 - colors[y0:y1, x0:x1]

    def _query_pixel_dims(self) -> None:
        """Query terminal cell pixel dimensions via XTWINOPS.

        Safe to call because telnetlib3 calls ``await stdout.drain()``
        after each ``stdout.write()`` (telnetlib3 >= 4.0.5), so stdout
        is idle between frames.  DCS query bytes will not interleave
        with sixel DCS / kitty APC frame data.
        """
        try:
            import blessed
            term = blessed.Terminal()
            px_h, px_w = term.get_sixel_height_and_width(timeout=0.3, force=True)
            if px_w > 0 and self._real_cols:
                self._cell_px_w = px_w // self._real_cols
            if px_h > 0 and self._real_rows:
                self._cell_px_h = px_h // self._real_rows
            log.debug("cell px queried: %dx%d", self._cell_px_w, self._cell_px_h)
        except Exception:
            pass

    def _render_full(self) -> None:
        """Render the full pyte screen (sync wrapper, dispatches to async)."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            self._render_frame_sync()
            return
        loop.create_task(self._render_frame())

    def _render_frame_sync(self) -> None:
        """Synchronous fallback when no event loop is available."""
        self._rendering = True
        try:
            self._do_render()
        finally:
            self._rendering = False

    async def _render_frame(self) -> None:
        """Render a frame asynchronously, awaiting transport drain."""
        self._rendering = True
        try:
            self._do_render()
            await self.inner.drain()
        finally:
            self._rendering = False

    def _do_render(self) -> None:
        """Build and write a single frame to the transport."""
        self._last_render_time = time.monotonic()
        self._render_scheduled = False
        self._render_timer = None

    
        if self._update_real_size():
            self._needs_full_redraw = True

        bitmap, colors = self._build_pixel_buffers()

        scale_w = max(1, (self._cell_px_w if self._cell_px_w > 0 else FONT_CELL_W) // FONT_CELL_W)
        scale_h = max(1, (self._cell_px_h if self._cell_px_h > 0 else FONT_CELL_H) // FONT_CELL_H)
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
            graphics_renderer.encode_kitty(colors, buf, columns=self.columns, rows=self.rows)
        else:
            graphics_renderer.encode_sixel(colors, buf)
        buf.write(SYNC_END)

        self._output(buf.getvalue())

    def _update_real_size(self) -> bool:
        """Re-query the real terminal size.

        :returns: ``True`` if the size changed.
        """
        real_rows, real_cols = terminal.get_terminal_size()
        changed = real_rows != self._real_rows or real_cols != self._real_cols
        if not changed:
            return False
        self._real_rows = real_rows
        self._real_cols = real_cols
        self._did_initial_clear = False
        self._need_px_query = True
        self._needs_full_redraw = True
        return True

    def schedule_resize(self, real_cols: int, real_rows: int) -> None:
        """Record a pending resize to be applied on the next write.

        Safe to call from a signal handler.

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

    def _schedule_render(self) -> None:
        """Schedule a render respecting the minimum frame interval.

        Skips if a render is already in progress, preventing concurrent
        writes that could corrupt sixel escape sequences.
        """
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
        """Start or cancel the blink timer based on cursor blink state.

        When the cursor is blinking and visible, a repeating 250 ms
        timer fires renders so the blink phase toggles even when no
        server data arrives.  The timer is cancelled when the cursor
        stops blinking or becomes hidden.
        """
        should_blink = self._cursor_blink
        if should_blink and self._blink_timer is None:
            loop = asyncio.get_event_loop()
            self._blink_timer = loop.call_later(0.5, self._on_blink_tick)
            log.debug("blink timer started")
        elif not should_blink and self._blink_timer is not None:
            self._blink_timer.cancel()
            self._blink_timer = None
            log.debug("blink timer cancelled")

    def _on_blink_tick(self) -> None:
        """Blink timer callback: render a frame and re-arm."""
        self._blink_timer = None
        log.debug("blink tick")
        self._schedule_render()
        self._manage_blink()

    def _cancel_blink(self) -> None:
        """Cancel the blink timer unconditionally."""
        if self._blink_timer is not None:
            self._blink_timer.cancel()
            self._blink_timer = None

    def write(self, data: bytes) -> None:
        """Decode, feed to pyte, and schedule a render.

        Called synchronously by telnetlib3's ``_raw_event_loop``.
        ``StreamWriter.write()`` is sync (buffers data); only
        ``drain()`` is async.  Rendering is dispatched to an async
        task via ``_render_full() -> create_task()`` so the frame
        build + ``drain()`` can be awaited there without blocking
        the event loop.
        """
        from . import client_shell

        if self.ctx.repl.ff_clears_screen:
            data = client_shell.replace_ff_with_clear(data)
        if self.ctx.repl.clear_homes_cursor:
            data = client_shell.inject_home_before_clear(data)

        self._apply_pending_resize()

        text = data.decode("utf-8", errors="replace")
        text = self._intercept_device_queries(text)
        text = self._handle_font_switch(text)
        prev_shape = self._cursor_shape
        prev_blink = self._cursor_blink
        text = self._handle_cursor_shape(text)
        shape_changed = (self._cursor_shape != prev_shape
                         or self._cursor_blink != prev_blink)
        text = CSI_WITH_INTERMEDIATE.sub("", text)
        text = XTGETTCAP_DCS_RE.sub("", text)

        if text:
            self.stream.feed(text)

        # DECTCEM (\033[?25h/l) is not used for graphics cursor visibility;
        # shells toggle it constantly during display updates.  The graphics
        # cursor is always visible (subject to blink/phase) and we ignore
        # pyte's cursor.hidden tracking.

        cursor_moved = (
            self.screen.cursor.x != self._prev_cursor_x
            or self.screen.cursor.y != self._prev_cursor_y
        )
        if cursor_moved and 0 <= self._prev_cursor_y < self.rows:
            self.screen.dirty.add(self._prev_cursor_y)
        self._prev_cursor_x = self.screen.cursor.x
        self._prev_cursor_y = self.screen.cursor.y

        self._manage_blink()

        if (self.screen.dirty or self._needs_full_redraw
                or cursor_moved or shape_changed):
            self._schedule_render()

    def resize(self, real_cols: int, real_rows: int) -> None:
        """Update real terminal bounds and force a full redraw.

        :param real_cols: Real terminal width in columns.
        :param real_rows: Real terminal height in rows.
        """
        self._real_cols = real_cols
        self._real_rows = real_rows
        self._needs_full_redraw = True
        self._schedule_render()

    def virtual_size(self) -> tuple[int, int]:
        """Return the virtual terminal size (rows, columns) for NAWS reporting."""
        return (self.rows, self.columns)

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)
