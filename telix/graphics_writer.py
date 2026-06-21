"""Graphics writer: pyte virtual terminal rendered via sixel/kitty graphics."""

import io
import time
import asyncio
import logging

import numpy as np

from . import graphics_renderer
from .graphics_writer_octant import SYNC_END, SYNC_START, BaseScreenWriter, pyte_color_to_rgb

log = logging.getLogger(__name__)

MIN_RENDER_INTERVAL = 0.033  # ~30 fps

FONT_CELL_W = 8
FONT_CELL_H = 16


class GraphicsWriter(BaseScreenWriter):
    """Renders BBS output as sixel or kitty terminal graphics.

    Each virtual character cell is rasterized as an 8x16 pixel glyph
    and transmitted as a terminal graphics frame.

    :param inner: The underlying ``asyncio.StreamWriter`` (real stdout).
    :param ctx: Session context with graphics configuration.
    :param protocol: Graphics protocol to use (``"kitty"`` or ``"sixel"``).
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

    # ------------------------------------------------------------------
    # hooks
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        if self._render_timer is not None:
            self._render_timer.cancel()
            self._render_timer = None
        self._cancel_blink()
        super().cleanup()

    # ------------------------------------------------------------------
    # glyph cache and pixel buffer building
    # ------------------------------------------------------------------

    def _ensure_glyph_cache(self) -> None:
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
        """Return ``(bitmap, colors)`` float32 arrays for the current screen."""
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
                    cp = max(0, min(255, cp))
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
        if self._cursor_blink:
            phase = int(time.monotonic() * 1000) % 1000
            if phase >= 500:
                return
        cx = self.screen.cursor.x
        cy = self.screen.cursor.y
        if not (0 <= cx < self.columns and 0 <= cy < self.rows):
            return
        py = cy * FONT_CELL_H
        px = cx * FONT_CELL_W
        shape = self._cursor_shape
        if shape in (0, 1, 2):
            y0, y1 = py, py + FONT_CELL_H
            x0, x1 = px, px + FONT_CELL_W
        elif shape in (3, 4):
            y0, y1 = py + FONT_CELL_H - 2, py + FONT_CELL_H
            x0, x1 = px, px + FONT_CELL_W
        else:
            y0, y1 = py, py + FONT_CELL_H
            x0, x1 = px, px + 2
        h, w = colors.shape[:2]
        y0 = max(0, y0)
        y1 = min(h, y1)
        x0 = max(0, x0)
        x1 = min(w, x1)
        if y1 > y0 and x1 > x0:
            colors[y0:y1, x0:x1] = 1.0 - colors[y0:y1, x0:x1]

    # ------------------------------------------------------------------
    # render dispatch
    # ------------------------------------------------------------------

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
