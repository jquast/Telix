#!/usr/bin/env python3
"""Diagnostic tool: dump a SyncTERM bitmap font as a visual grid of octant characters.

Each glyph (8x16 pixels) is rendered as 4 wide x 4 tall octant cells.  Glyphs
are arranged 16 per row (16 columns x 16 rows = 256 total), matching the
canonical Amiga Topaz reference layout.

Usage::

    python tools/dump_font.py [--font topaz] [--scale 1] [--labels]

Output is plain UTF-8 to stdout.
"""

import argparse
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from telix.graphics_bmpfont import OCTANT, glyph_to_octants, load_font  # noqa: E402
from telix.fonts.font_registry import FONT_BY_SHORT_NAME  # noqa: E402

GLYPH_COLS = 4  # octant cells per glyph (horizontal)
GLYPH_ROWS = 4  # octant cells per glyph (vertical)
CHARS_PER_ROW = 16
TOTAL_GLYPHS = 256


def render_glyph_octants(font, char_code: int) -> list[list[int]]:
    """Return 4x4 grid of octant pattern indices for a single glyph."""
    bitmap = font.glyph(char_code)
    return glyph_to_octants(bitmap)


def build_grid(font, show_labels: bool = False) -> str:
    """Build a text grid of all 256 glyphs, 16 per row."""
    # Pre-render all glyphs to octant grids
    all_glyphs = [render_glyph_octants(font, code) for code in range(TOTAL_GLYPHS)]

    glyph_rows = TOTAL_GLYPHS // CHARS_PER_ROW  # 16
    lines: list[str] = []

    # Column header labels
    if show_labels:
        header_parts = ["    "]
        for col in range(CHARS_PER_ROW):
            header_parts.append(f"  {col:02X}  ")
        lines.append("".join(header_parts))

    for glyph_row in range(glyph_rows):
        if show_labels:
            lines.append(f"    {'─' * (CHARS_PER_ROW * 6)}")

        # Each glyph is 4 octant rows tall
        for oct_row in range(GLYPH_ROWS):
            line_parts: list[str] = []

            # Row label
            if show_labels and oct_row == 0:
                line_parts.append(f"{glyph_row:02X}x ")

            for col in range(CHARS_PER_ROW):
                glyph_idx = glyph_row * CHARS_PER_ROW + col
                octants = all_glyphs[glyph_idx][oct_row]
                line_parts.append("".join(OCTANT[p] for p in octants))
                if show_labels:
                    line_parts.append("│")

            if show_labels and oct_row == 0:
                # Right row label
                line = "".join(line_parts) + f" x{glyph_row:02X}"
            else:
                line = "".join(line_parts)

            lines.append(line)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Dump a SyncTERM bitmap font as octant characters."
    )
    parser.add_argument(
        "--font", default="topaz",
        help="Font short name (default: topaz)"
    )
    parser.add_argument(
        "--labels", action="store_true",
        help="Show row/column hex labels"
    )
    args = parser.parse_args()

    try:
        entry = FONT_BY_SHORT_NAME[args.font]
    except KeyError:
        sys.exit(f"Unknown font: {args.font!r}")

    font = load_font(entry.font_id)
    print(f"Font: {font.name}  (id={font.font_id}, encoding={font.encoding})")
    print(f"Grid: {CHARS_PER_ROW} cols x {TOTAL_GLYPHS // CHARS_PER_ROW} rows, "
          f"glyph={GLYPH_COLS}x{GLYPH_ROWS} octants")
    print()

    grid = build_grid(font, show_labels=args.labels)
    print(grid)


if __name__ == "__main__":
    main()
