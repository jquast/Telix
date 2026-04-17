"""SyncTERM font registry.

Maps SyncTERM font IDs (0--44) to font metadata and binary offsets into
``syncterm_fonts.bin``.  Each 8x16 font is stored as 4096 contiguous bytes
(256 glyphs, 16 bytes per glyph, 1 byte per row, MSB-left).

Generated from SyncTERM ``allfonts.c`` by ``tools/extract_syncterm_fonts.py``.
"""

import typing

GLYPH_HEIGHT = 16
GLYPH_WIDTH = 8
GLYPHS_PER_FONT = 256
FONT_BYTES = GLYPHS_PER_FONT * GLYPH_HEIGHT  # 4096


class FontEntry(typing.NamedTuple):
    """Metadata for a single SyncTERM font."""

    font_id: int
    name: str
    short_name: str
    encoding: str
    bin_offset: int


# fmt: off
FONT_TABLE: list[FontEntry] = [
    FontEntry(0, "Codepage 437 English", "cp437", "cp437", 0),
    FontEntry(1, "Codepage 1251 Cyrillic, (swiss)", "cp1251-swiss", "cp1251", 4096),
    FontEntry(2, "Russian koi8-r", "koi8-r", "koi8-r", 8192),
    FontEntry(3, "ISO-8859-2 Central European", "iso-8859-2", "iso-8859-2", 12288),
    FontEntry(4, "ISO-8859-4 Baltic wide (VGA 9bit)", "iso-8859-4-wide", "iso-8859-4", 16384),
    FontEntry(5, "Codepage 866 (c) Russian", "cp866c", "cp866", 20480),
    FontEntry(6, "ISO-8859-9 Turkish", "iso-8859-9", "iso-8859-9", 24576),
    FontEntry(7, "haik8 Armenian", "haik8", "iso-8859-1", 28672),
    FontEntry(8, "ISO-8859-8 Hebrew", "iso-8859-8", "iso-8859-8", 32768),
    FontEntry(9, "Ukrainian koi8-u", "koi8-u", "koi8-u", 36864),
    FontEntry(10, "ISO-8859-15 West European, (thin)", "iso-8859-15-thin", "iso-8859-15", 40960),
    FontEntry(11, "ISO-8859-4 Baltic (VGA 9bit)", "iso-8859-4-vga", "iso-8859-4", 45056),
    FontEntry(12, "Russian koi8-r (b)", "koi8-r-b", "koi8-r", 49152),
    FontEntry(13, "ISO-8859-4 Baltic wide", "iso-8859-4-wide-b", "iso-8859-4", 53248),
    FontEntry(14, "ISO-8859-5 Cyrillic", "iso-8859-5", "iso-8859-5", 57344),
    FontEntry(15, "ARMSCII-8 Armenian", "armscii-8", "iso-8859-1", 61440),
    FontEntry(16, "ISO-8859-15 West European", "iso-8859-15", "iso-8859-15", 65536),
    FontEntry(17, "Codepage 850 Latin I, (thin)", "cp850-thin", "cp850", 69632),
    FontEntry(18, "Codepage 850 Latin I", "cp850", "cp850", 73728),
    FontEntry(19, "Codepage 865 Norwegian, (thin)", "cp865-thin", "cp865", 77824),
    FontEntry(20, "Codepage 1251 Cyrillic", "cp1251", "cp1251", 81920),
    FontEntry(21, "ISO-8859-7 Greek", "iso-8859-7", "iso-8859-7", 86016),
    FontEntry(22, "Russian koi8-r (c)", "koi8-r-c", "koi8-r", 90112),
    FontEntry(23, "ISO-8859-4 Baltic", "iso-8859-4", "iso-8859-4", 94208),
    FontEntry(24, "ISO-8859-1 West European", "iso-8859-1", "iso-8859-1", 98304),
    FontEntry(25, "Codepage 866 Russian", "cp866", "cp866", 102400),
    FontEntry(26, "Codepage 437 English, (thin)", "cp437-thin", "cp437", 106496),
    FontEntry(27, "Codepage 866 (b) Russian", "cp866b", "cp866", 110592),
    FontEntry(28, "Codepage 865 Norwegian", "cp865", "cp865", 114688),
    FontEntry(29, "Ukrainian cp866u", "cp866u", "cp866", 118784),
    FontEntry(30, "ISO-8859-1 West European, (thin)", "iso-8859-1-thin", "iso-8859-1", 122880),
    FontEntry(31, "Codepage 1131 Belarusian, (swiss)", "cp1131", "cp1131", 126976),
    FontEntry(32, "Commodore 64 (UPPER)", "c64-upper", "petscii", 131072),
    FontEntry(33, "Commodore 64 (Lower)", "c64-lower", "petscii", 135168),
    FontEntry(34, "Commodore 128 (UPPER)", "c128-upper", "petscii", 139264),
    FontEntry(35, "Commodore 128 (Lower)", "c128-lower", "petscii", 143360),
    FontEntry(36, "Atari", "atascii", "atascii", 147456),
    FontEntry(37, "P0T NOoDLE (Amiga)", "p0t-noodle", "iso-8859-1", 151552),
    FontEntry(38, "mO'sOul (Amiga)", "mosoul", "iso-8859-1", 155648),
    FontEntry(39, "MicroKnight Plus (Amiga)", "microknight-plus", "iso-8859-1", 159744),
    FontEntry(40, "Topaz Plus (Amiga)", "topaz-plus", "iso-8859-1", 163840),
    FontEntry(41, "MicroKnight (Amiga)", "microknight", "iso-8859-1", 167936),
    FontEntry(42, "Topaz (Amiga)", "topaz", "iso-8859-1", 172032),
    FontEntry(43, "Prestel", "prestel", "iso-8859-1", 176128),
    FontEntry(44, "Atari ST", "atari-st", "iso-8859-1", 180224),
]
# fmt: on

FONT_BY_ID: dict[int, FontEntry] = {e.font_id: e for e in FONT_TABLE}

FONT_BY_SHORT_NAME: dict[str, FontEntry] = {e.short_name: e for e in FONT_TABLE}

DEFAULT_FONT_ID = 0
