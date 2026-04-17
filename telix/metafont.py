"""Octant metafont rendering engine.

Converts bitmap font glyphs into Unicode octant block characters (2x4 sub-pixels
per terminal cell) for rendering BBS screens in modern terminals.

An 8x16 bitmap glyph becomes a 4-wide x 4-tall block of real terminal cells,
each cell encoding a 2x4 pixel region as a single octant character with
foreground and background colors.

Octant characters use Unicode 17.0 range U+1CD00--U+1CDE5 plus 26 legacy
block element characters for common patterns.
"""

import pathlib
from bisect import bisect_left

from .fonts import font_registry

FONT_BIN_PATH = pathlib.Path(__file__).parent / "fonts" / "syncterm_fonts.bin"

# 26 octant bit patterns that map to pre-existing block element characters.
# Bit layout in the 2x4 cell grid:
#
#     bit0  bit1
#     bit2  bit3
#     bit4  bit5
#     bit6  bit7
_OCTANT_SPECIALS: dict[int, int] = {
    0x00: 0x00A0,   # NO-BREAK SPACE
    0x01: 0x1CEA8,  # LEFT HALF UPPER ONE QUARTER BLOCK
    0x02: 0x1CEAB,  # RIGHT HALF UPPER ONE QUARTER BLOCK
    0x03: 0x1FB82,  # UPPER ONE QUARTER BLOCK
    0x05: 0x2598,   # QUADRANT UPPER LEFT
    0x0A: 0x259D,   # QUADRANT UPPER RIGHT
    0x0F: 0x2580,   # UPPER HALF BLOCK
    0x14: 0x1FBE6,  # MIDDLE LEFT ONE QUARTER BLOCK
    0x28: 0x1FBE7,  # MIDDLE RIGHT ONE QUARTER BLOCK
    0x3F: 0x1FB85,  # UPPER THREE QUARTERS BLOCK
    0x40: 0x1CEA3,  # LEFT HALF LOWER ONE QUARTER BLOCK
    0x50: 0x2596,   # QUADRANT LOWER LEFT
    0x55: 0x258C,   # LEFT HALF BLOCK
    0x5A: 0x259E,   # QUADRANT UPPER RIGHT AND LOWER LEFT
    0x5F: 0x259B,   # QUADRANT UPPER LEFT AND UPPER RIGHT AND LOWER LEFT
    0x80: 0x1CEA0,  # RIGHT HALF LOWER ONE QUARTER BLOCK
    0xA0: 0x2597,   # QUADRANT LOWER RIGHT
    0xA5: 0x259A,   # QUADRANT UPPER LEFT AND LOWER RIGHT
    0xAA: 0x2590,   # RIGHT HALF BLOCK
    0xAF: 0x259C,   # QUADRANT UPPER LEFT AND UPPER RIGHT AND LOWER RIGHT
    0xC0: 0x2582,   # LOWER ONE QUARTER BLOCK
    0xF0: 0x2584,   # LOWER HALF BLOCK
    0xF5: 0x2599,   # QUADRANT UPPER LEFT AND LOWER LEFT AND LOWER RIGHT
    0xFA: 0x259F,   # QUADRANT UPPER RIGHT AND LOWER LEFT AND LOWER RIGHT
    0xFC: 0x2586,   # LOWER THREE QUARTERS BLOCK
    0xFF: 0x2588,   # FULL BLOCK
}

_SPECIAL_KEYS_SORTED: list[int] = sorted(_OCTANT_SPECIALS)


def build_octant_table() -> list[str]:
    """Build lookup table mapping 8-bit pattern to Unicode octant character.

    Bit positions correspond to the 2x4 cell grid::

        bit0  bit1
        bit2  bit3
        bit4  bit5
        bit6  bit7

    26 patterns map to pre-existing block element characters; the remaining
    230 patterns map to U+1CD00..U+1CDE5.
    """
    table = [""] * 256
    for i in range(256):
        if i in _OCTANT_SPECIALS:
            table[i] = chr(_OCTANT_SPECIALS[i])
        else:
            offset = bisect_left(_SPECIAL_KEYS_SORTED, i)
            table[i] = chr(0x1CD00 + i - offset)
    return table


OCTANT = build_octant_table()


class BitmapFont:
    """An 8x16 bitmap font loaded from the SyncTERM font binary.

    :param font_id: SyncTERM font ID (0--44).
    :param data: Raw bitmap bytes (4096 bytes, 256 glyphs x 16 rows).
    :param name: Human-readable font name.
    :param encoding: Python codec name for the font's codepage.
    """

    def __init__(self, font_id: int, data: bytes, name: str, encoding: str) -> None:
        self.font_id = font_id
        self.data = data
        self.name = name
        self.encoding = encoding

    def glyph(self, char_code: int) -> list[int]:
        """Return the 16-row bitmap for *char_code* (0--255).

        Each int is an 8-bit mask, MSB = leftmost pixel.
        """
        offset = char_code * font_registry.GLYPH_HEIGHT
        return list(self.data[offset:offset + font_registry.GLYPH_HEIGHT])


_font_cache: dict[int, BitmapFont] = {}
_font_bin_data: bytes | None = None


def load_font_bin() -> bytes:
    global _font_bin_data
    if _font_bin_data is None:
        _font_bin_data = FONT_BIN_PATH.read_bytes()
    return _font_bin_data


def load_font(font_id: int) -> BitmapFont:
    """Load a SyncTERM font by ID, with caching.

    :param font_id: SyncTERM font ID (0--44).
    :returns: Loaded bitmap font.
    :raises KeyError: If *font_id* is not in the registry.
    """
    if font_id in _font_cache:
        return _font_cache[font_id]

    entry = font_registry.FONT_BY_ID[font_id]
    bin_data = load_font_bin()
    font_data = bin_data[entry.bin_offset:entry.bin_offset + font_registry.FONT_BYTES]
    font = BitmapFont(font_id, font_data, entry.name, entry.encoding)
    _font_cache[font_id] = font
    return font


def glyph_to_octants(bitmap: list[int]) -> list[list[int]]:
    """Convert a 16-row glyph bitmap to a 4x4 grid of octant bit patterns.

    :param bitmap: 16 ints, each an 8-bit row (MSB = leftmost pixel).
    :returns: 4 rows of 4 octant indices (0--255), suitable for ``OCTANT[]`` lookup.

    The bitmap is divided into a 4x4 grid of 2x4 pixel blocks::

        cols 0-1  cols 2-3  cols 4-5  cols 6-7
        rows 0-3  rows 0-3  rows 0-3  rows 0-3   -> octant row 0
        rows 4-7  rows 4-7  rows 4-7  rows 4-7   -> octant row 1
        rows 8-11 ...                              -> octant row 2
        rows 12-15 ...                             -> octant row 3

    Within each 2x4 block, the octant bit pattern is::

        bit0 = pixel(col+0, row+0)    bit1 = pixel(col+1, row+0)
        bit2 = pixel(col+0, row+1)    bit3 = pixel(col+1, row+1)
        bit4 = pixel(col+0, row+2)    bit5 = pixel(col+1, row+2)
        bit6 = pixel(col+0, row+3)    bit7 = pixel(col+1, row+3)
    """
    result: list[list[int]] = []
    for oct_row in range(4):
        row_base = oct_row * 4
        row_patterns: list[int] = []
        for oct_col in range(4):
            col_shift = 6 - oct_col * 2  # bit positions: 6, 4, 2, 0
            pattern = 0
            for sub_row in range(4):
                row_byte = bitmap[row_base + sub_row] if (row_base + sub_row) < len(bitmap) else 0
                left_pixel = (row_byte >> (col_shift + 1)) & 1
                right_pixel = (row_byte >> col_shift) & 1
                bit_offset = sub_row * 2
                pattern |= left_pixel << bit_offset
                pattern |= right_pixel << (bit_offset + 1)
            row_patterns.append(pattern)
        result.append(row_patterns)
    return result


def render_cell(
    char_code: int,
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
    font: BitmapFont,
) -> list[str]:
    """Render a single BBS character as 4 lines of 4 octant characters each.

    :param char_code: Character code (0--255) in the font's codepage.
    :param fg: Foreground RGB color tuple.
    :param bg: Background RGB color tuple.
    :param font: Bitmap font to use.
    :returns: 4 strings, each containing 4 octant characters with embedded ANSI SGR.

    Each returned string includes the SGR sequence to set fg/bg colors, followed
    by 4 octant characters.  The caller is responsible for cursor positioning.
    """
    bitmap = font.glyph(char_code)
    octant_grid = glyph_to_octants(bitmap)
    sgr = f"\033[38;2;{fg[0]};{fg[1]};{fg[2]};48;2;{bg[0]};{bg[1]};{bg[2]}m"
    lines: list[str] = []
    for row in octant_grid:
        line = sgr + "".join(OCTANT[p] for p in row)
        lines.append(line)
    return lines


# Columns and rows of real terminal cells needed per BBS character.
CELLS_PER_CHAR_X = 4  # 8 pixels / 2 pixels per octant column
CELLS_PER_CHAR_Y = 4  # 16 pixels / 4 pixels per octant row
