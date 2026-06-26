#!/usr/bin/env python3
"""Dump a bitmap font as rendered via kitty or sixel terminal graphics.

Glyphs are arranged 16 per row, scaled for pixel-sharp visibility.
Uses the same rendering pipeline as GraphicsWriter._do_render.

Usage::

    python tools/dump_font.py              # default font (0)
    python tools/dump_font.py --font 36    # ATASCII
    python tools/dump_font.py --font topaz

Output is kitty or sixel graphics to stdout.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from telix.graphics_writer import load_font, SYNC_START, SYNC_END  # noqa: E402
from telix.fonts import font_registry  # noqa: E402
from telix import graphics_renderer  # noqa: E402

CHARS_PER_ROW = 16
SCALE = 3


def find_font(key: str) -> int:
    """Resolve a font identifier (numeric ID or name substring) to a font_id."""
    try:
        font_id = int(key)
        if font_id in {e.font_id for e in font_registry.FONT_TABLE}:
            return font_id
    except ValueError:
        pass
    lower = key.lower()
    for entry in font_registry.FONT_TABLE:
        if lower in entry.name.lower():
            return entry.font_id
    sys.exit(f"Unknown font: {key!r}")


def render_grid(font) -> None:
    """Render all glyphs as a pixel-sharp kitty or sixel image."""
    import numpy as np
    import blessed

    nglyphs = font.nglyphs
    glyph_rows = (nglyphs + CHARS_PER_ROW - 1) // CHARS_PER_ROW
    fh = font.height
    fw = font.width

    img_h = glyph_rows * fh
    img_w = CHARS_PER_ROW * fw

    colors = np.zeros((img_h, img_w, 3), dtype=np.float32)
    fg_buf = np.array([1.0, 1.0, 1.0], dtype=np.float32)

    for glyph_row in range(glyph_rows):
        for col in range(CHARS_PER_ROW):
            idx = glyph_row * CHARS_PER_ROW + col
            if idx >= nglyphs:
                continue
            rows = font.glyph(idx)
            py = glyph_row * fh
            px = col * fw
            for r in range(fh):
                val = rows[r] if r < len(rows) else 0
                for c in range(fw):
                    if (val >> (fw - 1 - c)) & 1:
                        colors[py + r, px + c] = fg_buf

    colors = np.repeat(np.repeat(colors, SCALE, axis=0), SCALE, axis=1)

    term = blessed.Terminal()
    protocol = graphics_renderer.detect_graphics_protocol(term)
    if protocol is None:
        sys.exit("Terminal does not support kitty or sixel graphics.")

    sys.stdout.write(SYNC_START)
    sys.stdout.write("\033[H\033[2J")
    sys.stdout.write(SYNC_END)
    sys.stdout.flush()

    sys.stdout.write(SYNC_START)
    sys.stdout.write("\033[H")
    if protocol == "kitty":
        graphics_renderer.encode_kitty(colors, sys.stdout)
    else:
        graphics_renderer.encode_sixel(colors, sys.stdout)
    sys.stdout.write(SYNC_END)
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump a bitmap font via terminal graphics.")
    parser.add_argument("--font", default="0", help="Font id or name substring (default: 0)")
    args = parser.parse_args()

    font_id = find_font(args.font)
    font = load_font(font_id)
    render_grid(font)


if __name__ == "__main__":
    main()
