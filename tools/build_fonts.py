"""Build telix/fonts/fonts.bin and font_registry.py from upstream bitmap font sources.

Downloads from GitHub (hoard-of-bitfonts, amigafonts) and int10h.org, caching to
``telix/fonts/deps/`` (git-ignored). Uses requests with retry for resilience.

Usage::

    python tools/build_fonts.py

Outputs:
    telix/fonts/fonts.bin       -- self-describing binary font archive
    telix/fonts/font_registry.py -- Python font index module
"""

import io
import os
import re
import struct
import zipfile
import pathlib
import logging

import requests
import urllib3.util
from fontTools.ttLib import TTFont
import monobit

log = logging.getLogger(__name__)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
FONTS_DIR = PROJECT_ROOT / "telix" / "fonts"
DEPS_DIR = FONTS_DIR / "deps"

CONNECT_TIMEOUT = int(os.environ.get("CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = int(os.environ.get("READ_TIMEOUT", "30"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "10"))
BACKOFF_FACTOR = float(os.environ.get("BACKOFF_FACTOR", "1.0"))

MAGIC = b"TELX"
VERSION = 1

CODEPAGE_ENCODING: dict[int, str] = {
    0: "cp437",
    1: "cp1251",
    2: "koi8-r",
    3: "iso-8859-2",
    4: "iso-8859-4",
    5: "cp866",
    6: "iso-8859-9",
    7: "iso-8859-1",
    8: "iso-8859-8",
    9: "koi8-u",
    10: "iso-8859-15",
    11: "iso-8859-4",
    12: "koi8-r",
    13: "iso-8859-4",
    14: "iso-8859-5",
    15: "iso-8859-1",
    16: "iso-8859-15",
    17: "cp850",
    18: "cp850",
    19: "cp865",
    20: "cp1251",
    21: "iso-8859-7",
    22: "koi8-r",
    23: "iso-8859-4",
    24: "iso-8859-1",
    25: "cp866",
    26: "cp437",
    27: "cp866",
    28: "cp865",
    29: "cp866",
    30: "iso-8859-1",
    31: "cp1131",
}

FONT_NAMES: dict[int, str] = {
    0: "CP437 English",
    1: "CP1251 Cyrillic (swiss)",
    2: "KOI8-R Russian",
    3: "ISO-8859-2 Central European",
    4: "ISO-8859-4 Baltic wide (VGA 9bit)",
    5: "CP866 Russian (c)",
    6: "ISO-8859-9 Turkish",
    7: "ISO-8859-1 West European",
    8: "ISO-8859-8 Hebrew",
    9: "KOI8-U Ukrainian",
    10: "ISO-8859-15 West European (thin)",
    11: "ISO-8859-4 Baltic (VGA 9bit)",
    12: "KOI8-R Russian (b)",
    13: "ISO-8859-4 Baltic wide",
    14: "ISO-8859-5 Cyrillic",
    15: "ISO-8859-1 West European",
    16: "ISO-8859-15 West European",
    17: "CP850 Multilingual Latin I (thin)",
    18: "CP850 Multilingual Latin I",
    19: "CP865 Norwegian (thin)",
    20: "CP1251 Cyrillic",
    21: "ISO-8859-7 Greek",
    22: "KOI8-R Russian (c)",
    23: "ISO-8859-4 Baltic",
    24: "ISO-8859-1 West European",
    25: "CP866 Russian",
    26: "CP437 English (thin)",
    27: "CP866 Russian (b)",
    28: "CP865 Norwegian",
    29: "CP866 Ukrainian",
    30: "ISO-8859-1 West European (thin)",
    31: "CP1131 Belarusian (swiss)",
}

FONT_DEFS = [
    (32, "../hoard-of-bitfonts/commodore/c64-c16-c128/c64.yaff", "Commodore 64 (UPPER)", "petscii", 0, 256),
    (33, "../hoard-of-bitfonts/commodore/c64-c16-c128/c64.yaff", "Commodore 64 (Lower)", "petscii", 256, 256),
    (34, "../hoard-of-bitfonts/commodore/c64-c16-c128/c128.yaff", "Commodore 128 (UPPER)", "petscii", 0, 256),
    (35, "../hoard-of-bitfonts/commodore/c64-c16-c128/c128.yaff", "Commodore 128 (Lower)", "petscii", 256, 256),
    (36, "../hoard-of-bitfonts/atari/8-bit/atascii.yaff", "Atari ATASCII", "atascii", 0, 256),
    (37, "amigafonts/psf1/P0T-NOoDLE_v1.0.psf.gz", "P0T NOoDLE (Amiga)", "iso-8859-1", 0, 256),
    (38, "amigafonts/psf1/mO'sOul_v1.0.psf.gz", "mO'sOul (Amiga)", "iso-8859-1", 0, 256),
    (39, "amigafonts/psf1/MicroKnightPlus_v1.0.psf.gz", "MicroKnight Plus (Amiga)", "iso-8859-1", 0, 256),
    (40, "amigafonts/psf1/TopazPlus_a500_v1.0.psf.gz", "Topaz Plus (Amiga)", "iso-8859-1", 0, 256),
    (41, "amigafonts/psf1/MicroKnight_v1.0.psf.gz", "MicroKnight (Amiga)", "iso-8859-1", 0, 256),
    (42, "amigafonts/psf1/Topaz_a500_v1.0.psf.gz", "Topaz (Amiga)", "iso-8859-1", 0, 256),
    (43, "../hoard-of-bitfonts/teletext/saa5050-uk.yaff", "Prestel", "iso-8859-1", 0, 256),
    (44, "../hoard-of-bitfonts/atari/st/atari-st-8x16.yaff", "Atari ST", "iso-8859-1", 0, 256),
]

HOARD_BASE = "https://raw.githubusercontent.com/robhagemans/hoard-of-bitfonts/master"
AMIGA_BASE = "https://raw.githubusercontent.com/rewtnull/amigafonts/master"
INT10H_URL = "https://int10h.org/oldschool-pc-fonts/download/oldschool_pc_font_pack_v2.2_FULL.zip"


def get_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.setdefault("User-Agent", "Mozilla/5.0 (compatible; telix-build-fonts/1.0)")
    retries = urllib3.util.Retry(
        total=MAX_RETRIES,
        connect=MAX_RETRIES,
        read=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        backoff_jitter=BACKOFF_FACTOR,
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=True,
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch(url: str, dest: pathlib.Path) -> None:
    if dest.exists():
        log.debug("Using cached %s", dest.name)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("Fetching %s", url)
    session = get_http_session()
    resp = session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    log.info("Saved %s (%d bytes)", dest.name, len(resp.content))


def _strip_size_suffix(name: str) -> str:
    return re.sub(r"\s+\d+x\d+$", "", name)




def parse_otb_vga(data: bytes) -> list[bytes]:
    font = TTFont(io.BytesIO(data))
    ebdt = font["EBDT"]
    cmap = font.getBestCmap()
    sd = ebdt.strikeData[0]

    unicode_glyphs: dict[int, bytes] = {}
    for cp, glyph_name in cmap.items():
        if glyph_name in sd:
            unicode_glyphs[cp] = sd[glyph_name].data

    glyphs: list[bytes] = []
    for b in range(256):
        try:
            cp = ord(bytes([b]).decode("cp437"))
        except (UnicodeDecodeError, LookupError):
            glyphs.append(b"\x00" * 16)
            continue
        glyphs.append(unicode_glyphs.get(cp, b"\x00" * 16))
    return glyphs




def build_codepage_variant(base_glyphs: list[bytes], encoding: str) -> list[bytes]:
    glyphs = list(base_glyphs)
    if len(glyphs) < 256:
        glyphs.extend([b"\x00" * 16] * (256 - len(glyphs)))

    unicode_to_cp437_glyph: dict[int, int] = {}
    for b in range(256):
        try:
            unicode_to_cp437_glyph[ord(bytes([b]).decode("cp437"))] = b
        except (UnicodeDecodeError, LookupError):
            pass

    for b in range(128, 256):
        try:
            cp = ord(bytes([b]).decode(encoding))
            if cp in unicode_to_cp437_glyph:
                glyphs[b] = base_glyphs[unicode_to_cp437_glyph[cp]]
        except (UnicodeDecodeError, LookupError):
            pass

    return glyphs




def load_font_file(path: pathlib.Path, glyph_offset: int, nglyphs: int) -> tuple[str, int, int, list[bytes]]:
    """Load a font via monobit and return (name, width, height, glyphs_list).

    Glyphs are extracted in codepoint order from *glyph_offset* for *nglyphs* entries.
    """
    pack = monobit.load(str(path))
    font = pack[0]

    rs = font.raster_size
    cs = font.cell_size
    width = rs.x if rs and rs.x else (cs.x if cs else 8)
    height = rs.y if rs and rs.y else (cs.y if cs else 8)
    name = _strip_size_suffix(font.name)

    glyph_dict: dict[int, bytes] = {}
    for glyph in font.glyphs:
        cp_bytes = glyph.codepoint.value
        if len(cp_bytes) == 1:
            glyph_dict[cp_bytes[0]] = glyph.as_bytes()

    glyphs: list[bytes] = []
    for i in range(glyph_offset, glyph_offset + nglyphs):
        glyphs.append(glyph_dict.get(i, b"\x00" * height))

    return name, width, height, glyphs


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    DEPS_DIR.mkdir(parents=True, exist_ok=True)
    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    fonts: list[tuple[int, str, str, int, int, int, bytes]] = []

    int10h_zip = DEPS_DIR / "oldschool_pc_font_pack_v2.2_FULL.zip"
    local_int10h = pathlib.Path("/tmp/oldschool_pc_fonts.zip")
    if local_int10h.exists() and not int10h_zip.exists():
        log.info("Copying %s to deps cache", local_int10h)
        int10h_zip.write_bytes(local_int10h.read_bytes())
    else:
        fetch(INT10H_URL, int10h_zip)
    with zipfile.ZipFile(int10h_zip) as zf:
        otb_data = zf.read("otb - Bm (linux bitmap)/Bm437_IBM_VGA_8x16.otb")

    base_glyphs = parse_otb_vga(otb_data)
    log.info("Extracted IBM VGA 8x16 base font: %d glyphs", len(base_glyphs))

    for font_id in range(32):
        encoding = CODEPAGE_ENCODING.get(font_id, "cp437")
        glyph_data = build_codepage_variant(base_glyphs, encoding)
        raw = b"".join(glyph_data)
        name = FONT_NAMES.get(font_id, encoding.upper())
        fonts.append((font_id, name, encoding, 8, 16, 256, raw))
        log.info("  [%2d] %s (%s) - %d bytes", font_id, name, encoding, len(raw))

    hoard_local = PROJECT_ROOT.parent / "hoard-of-bitfonts"
    amiga_local = pathlib.Path.home() / "Source.Progs" / "amigafonts"

    for font_id, rel_path, name, encoding, glyph_off, fn_glyphs in FONT_DEFS:
        if rel_path.startswith("../hoard-of-bitfonts/"):
            subpath = rel_path[len("../hoard-of-bitfonts/"):]
            local_dest = hoard_local / subpath
            if local_dest.exists():
                dest = local_dest
            else:
                dest = DEPS_DIR / "hoard" / subpath
                fetch(f"{HOARD_BASE}/{subpath}", dest)
        elif rel_path.startswith("amigafonts/"):
            subpath = rel_path[len("amigafonts/"):]
            filename = pathlib.Path(rel_path).name
            local_dest = amiga_local / subpath
            if local_dest.exists():
                dest = local_dest
            else:
                dest = DEPS_DIR / "amiga" / filename
                fetch(f"{AMIGA_BASE}/{subpath}", dest)
        else:
            dest = pathlib.Path(rel_path)

        font_name, width, height, glyphs = load_font_file(dest, glyph_off, fn_glyphs)
        if font_name and "system font" not in font_name:
            name = font_name
        raw = b"".join(glyphs)
        fonts.append((font_id, name, encoding, width, height, fn_glyphs, raw))
        log.info("  [%2d] %s (%s) %dx%d %d glyphs - %d bytes", font_id, name, encoding, width, height, fn_glyphs, len(raw))

    fonts.sort(key=lambda x: x[0])
    bin_path = FONTS_DIR / "fonts.bin"
    name_strings = bytearray()
    font_data = bytearray()
    directory = bytearray()

    data_offset = 0
    for fid, name, encoding, width, height, nglyphs, raw in fonts:
        name_bytes = name.encode("utf-8")
        encoding_bytes = encoding.encode("utf-8")
        name_off = len(name_strings)
        name_strings.extend(name_bytes)
        name_strings.extend(encoding_bytes)

        directory.extend(
            struct.pack(
                "<HBBHHHHII",
                fid, width, height, nglyphs,
                name_off, len(name_bytes), len(encoding_bytes),
                data_offset, len(raw),
            )
        )
        font_data.extend(raw)
        data_offset += len(raw)

    num_fonts = len(fonts)

    with open(bin_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<HH", VERSION, num_fonts))
        f.write(directory)
        f.write(name_strings)
        f.write(font_data)
    log.info("Wrote %s: %d fonts, %d bytes", bin_path, num_fonts, bin_path.stat().st_size)

    reg_path = FONTS_DIR / "font_registry.py"
    _dir_start = 8
    _entry = struct.Struct("<HBBHHHHII")
    _name_start = _dir_start + num_fonts * _entry.size
    _raw = bin_path.read_bytes()
    data_base = _dir_start + num_fonts * _entry.size + len(name_strings)

    entries: list[str] = []
    for _i in range(num_fonts):
        _off = _dir_start + _i * _entry.size
        _fid, _w, _h, _ng, _noff, _nlen, _elen, _doff, _dlen = _entry.unpack_from(_raw, _off)
        _name = _raw[_name_start + _noff : _name_start + _noff + _nlen].decode("utf-8")
        _enc = _raw[_name_start + _noff + _nlen : _name_start + _noff + _nlen + _elen].decode("utf-8")
        entries.append(f"    FontEntry({_fid}, {_name!r}, {_enc!r}, {_w}, {_h}, {_ng}, {_doff + data_base}, {_dlen})")

    with open(reg_path, "w") as f:
        f.write('"""Bitmap font registry -- auto-generated by tools/build_fonts.py."""\n\n')
        f.write("import pathlib\nimport typing\n\n")
        f.write("FONT_BIN_PATH = pathlib.Path(__file__).parent / 'fonts.bin'\n")
        f.write(f"FONT_MAGIC = {MAGIC!r}\n")
        f.write(f"FONT_VERSION = {VERSION}\n\n\n")
        f.write("class FontEntry(typing.NamedTuple):\n")
        f.write("    font_id: int\n")
        f.write("    name: str\n")
        f.write("    encoding: str\n")
        f.write("    width: int\n")
        f.write("    height: int\n")
        f.write("    nglyphs: int\n")
        f.write("    data_offset: int\n")
        f.write("    data_len: int\n\n\n")
        f.write("FONT_TABLE: list[FontEntry] = [\n")
        f.write(",\n".join(entries))
        f.write("\n]\n\n")
        f.write("DEFAULT_FONT_ID = 0\n")
    log.info("Wrote %s", reg_path)


if __name__ == "__main__":
    main()
