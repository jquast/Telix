#!/usr/bin/env python3
"""Extract bitmap font data from SyncTERM's allfonts.c.

Parses the C struct array and writes a compact binary file containing
all font bitmaps (8x16 only) plus a Python registry module.

Usage::

    python tools/extract_syncterm_fonts.py ~/Source.Progs/syncterm-repo/src/conio/allfonts.c

Outputs:
    telix/fonts/syncterm_fonts.bin   -- raw 8x16 bitmaps, 4096 bytes per font
    telix/fonts/font_registry.py     -- font ID -> name, codepage, offset mapping
"""

import re
import sys
import struct
import pathlib

FONT_DEFS_SECTION = re.compile(
    r'CIOLIBEXPORT struct conio_font_data_struct conio_fontdata\[257\] = \{',
)

# Match a C hex string like "\x00\x7e\x81..."
HEX_ESCAPE = re.compile(r'\\x([0-9a-fA-F]{2})')

# Match the metadata line: , "Font Name", CIOLIB_XXXX, true/false}
META_RE = re.compile(
    r',\s*"([^"]+)"\s*,\s*(CIOLIB_\w+)\s*,\s*(true|false)\s*\}'
)
META_NULL_RE = re.compile(
    r',\s*NULL\s*,\s*(CIOLIB_\w+)\s*,\s*(true|false)\s*\}'
)

# Codepage enum -> Python encoding name
CODEPAGE_MAP = {
    "CIOLIB_CP437": "cp437",
    "CIOLIB_CP850": "cp850",
    "CIOLIB_CP865": "cp865",
    "CIOLIB_CP1251": "cp1251",
    "CIOLIB_CP1131": "cp1131",
    "CIOLIB_CP866M": "cp866",
    "CIOLIB_CP866M2": "cp866",
    "CIOLIB_CP866U": "cp866",
    "CIOLIB_KOI8_R": "koi8-r",
    "CIOLIB_KOI8_U": "koi8-u",
    "CIOLIB_ISO_8859_1": "iso-8859-1",
    "CIOLIB_ISO_8859_2": "iso-8859-2",
    "CIOLIB_ISO_8859_4": "iso-8859-4",
    "CIOLIB_ISO_8859_5": "iso-8859-5",
    "CIOLIB_ISO_8859_7": "iso-8859-7",
    "CIOLIB_ISO_8859_8": "iso-8859-8",
    "CIOLIB_ISO_8859_9": "iso-8859-9",
    "CIOLIB_ISO_8859_15": "iso-8859-15",
    "CIOLIB_ARMSCII8": "iso-8859-1",
    "CIOLIB_HAIK8": "iso-8859-1",
    "CIOLIB_PETSCIIU": "petscii",
    "CIOLIB_PETSCIIL": "petscii",
    "CIOLIB_ATASCII": "atascii",
    "CIOLIB_ATARIST": "iso-8859-1",
    "CIOLIB_PRESTEL": "iso-8859-1",
    "CIOLIB_PRESTEL_SEP": "iso-8859-1",
}


def extract_hex_bytes(lines):
    """Extract raw bytes from consecutive C hex string lines."""
    data = bytearray()
    for line in lines:
        for match in HEX_ESCAPE.finditer(line):
            data.append(int(match.group(1), 16))
    return bytes(data)


def parse_allfonts(source_path):
    """Parse allfonts.c and yield (font_id, name, codepage, bitmap_8x16) tuples."""
    text = pathlib.Path(source_path).read_text(encoding="latin-1")
    lines = text.splitlines()

    # Find the #else (non-NO_FONTS) section with actual data
    in_data_section = False
    font_id = 0
    current_hex_lines = []
    bitmaps_collected = 0
    current_name = None
    current_cp = None

    for i, line in enumerate(lines):
        if "conio_fontdata[257]" in line and "#else" not in lines[max(0, i - 5) : i]:
            # Skip the NO_FONTS section
            pass

        if line.strip() == "#else":
            in_data_section = True
            continue

        if not in_data_section:
            continue

        if line.strip() == "#endif":
            break

        # Accumulate hex data
        if "\\x" in line:
            current_hex_lines.append(line)
            continue

        # Check for metadata line
        meta = META_RE.search(line)
        meta_null = META_NULL_RE.search(line) if not meta else None

        if meta or meta_null:
            # Extract the bitmap data collected so far
            raw = extract_hex_bytes(current_hex_lines)
            current_hex_lines = []

            if meta:
                current_name = meta.group(1)
                current_cp = meta.group(2)
            else:
                current_name = None
                current_cp = meta_null.group(1)

            # The raw data may contain 8x16 + 8x14 + 8x8 + 12x20 concatenated
            # 8x16 is always first: 256 glyphs * 16 bytes = 4096 bytes
            bitmap_8x16 = raw[:4096] if len(raw) >= 4096 else None

            if current_name:
                yield (font_id, current_name, current_cp, bitmap_8x16)

            font_id += 1

        # Handle NULL-only entries (fonts 45-255 with no data)
        if "{NULL, NULL, NULL, NULL, NULL," in line:
            font_id += 1


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path/to/allfonts.c>", file=sys.stderr)
        sys.exit(1)

    source_path = sys.argv[1]
    fonts_dir = pathlib.Path(__file__).resolve().parent.parent / "telix" / "fonts"
    fonts_dir.mkdir(exist_ok=True)

    fonts = list(parse_allfonts(source_path))
    print(f"Extracted {len(fonts)} fonts")

    # Write binary file: concatenated 8x16 bitmaps for fonts that have them
    bin_path = fonts_dir / "syncterm_fonts.bin"
    registry = []

    with open(bin_path, "wb") as f:
        offset = 0
        for font_id, name, codepage, bitmap in fonts:
            encoding = CODEPAGE_MAP.get(codepage, "cp437")
            if bitmap and len(bitmap) == 4096:
                f.write(bitmap)
                registry.append((font_id, name, encoding, codepage, offset))
                offset += 4096
                print(f"  [{font_id:2d}] {name} ({encoding}) - 4096 bytes")
            else:
                registry.append((font_id, name, encoding, codepage, None))
                print(f"  [{font_id:2d}] {name} ({encoding}) - NO 8x16 DATA")

    print(f"\nWrote {bin_path} ({offset} bytes)")

    # Write Python registry module
    reg_path = fonts_dir / "font_registry.py"
    with open(reg_path, "w") as f:
        f.write('"""SyncTERM font registry -- auto-generated by tools/extract_syncterm_fonts.py."""\n\n')
        f.write("# (font_id, name, encoding, bin_offset_or_None)\n")
        f.write("FONT_TABLE: list[tuple[int, str, str, int | None]] = [\n")
        for font_id, name, encoding, codepage, off in registry:
            f.write(f"    ({font_id}, {name!r}, {encoding!r}, {off}),\n")
        f.write("]\n\n")
        f.write("FONT_BY_ID: dict[int, tuple[str, str, int | None]] = {\n")
        f.write("    fid: (name, enc, off) for fid, name, enc, off in FONT_TABLE\n")
        f.write("}\n\n")
        f.write("# Short name -> font_id for encoding/font selector\n")
        f.write("FONT_NAMES: dict[str, int] = {\n")
        for font_id, name, encoding, codepage, off in registry:
            if off is not None:
                # Create a short key
                short = name.lower()
                f.write(f"    {short!r}: {font_id},\n")
        f.write("}\n")

    print(f"Wrote {reg_path}")


if __name__ == "__main__":
    main()
