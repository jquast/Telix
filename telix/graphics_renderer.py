"""Sixel and Kitty graphics protocol encoders.

Adapted from dapple_ (MIT license, Copyright (c) 2025 Alexander Towell).

.. _dapple: https://github.com/queelius/dapple
"""

import io
import os
import zlib
import base64
import logging

import numpy as np

log = logging.getLogger(__name__)

DCS_START = "\033Pq"
DCS_END = "\033\\"
APC_START = "\033_G"
APC_END = "\033\\"
MAX_CHUNK_SIZE = 4096


def detect_kitty(term) -> bool:
    """Check whether *term* supports the Kitty graphics protocol.

    :param term: A :class:`blessed.Terminal` instance.
    :returns: ``True`` if kitty graphics is supported.
    """
    try:
        return term.does_kitty_graphics(timeout=0.5)
    except Exception:
        return bool(os.environ.get("KITTY_WINDOW_ID") or os.environ.get("GHOSTTY_RESOURCES_DIR"))


def detect_sixel(term) -> bool:
    """Check whether *term* supports sixel graphics.

    :param term: A :class:`blessed.Terminal` instance.
    :returns: ``True`` if sixel graphics is supported.
    """
    try:
        return term.does_sixel(timeout=0.5)
    except Exception:
        pass
    term_name = os.environ.get("TERM", "").lower()
    term_prog = os.environ.get("TERM_PROGRAM", "").lower()
    for name in ("mlterm", "yaft", "foot", "contour", "wezterm", "mintty"):
        if name in term_name or name in term_prog:
            return True
    return bool("xterm" in term_name and os.environ.get("XTERM_VERSION"))


def detect_graphics_protocol(term) -> str | None:
    """Detect the best available graphics protocol.

    Kitty is preferred (better compression via PNG). Sixel is the fallback.
    Set :envvar:`TELIX_FORCE_SIXEL` to ``"1"`` to test sixel even when
    kitty is available.

    :param term: A :class:`blessed.Terminal` instance.
    :returns: ``"kitty"``, ``"sixel"``, or ``None`` if neither is supported.
    """
    if os.environ.get("TELIX_FORCE_SIXEL") == "1":
        if detect_sixel(term):
            log.debug("Graphics protocol: sixel (forced)")
            return "sixel"
        log.debug("Graphics protocol: none (sixel forced but unavailable)")
        return None
    if detect_kitty(term):
        log.debug("Graphics protocol: kitty")
        return "kitty"
    if detect_sixel(term):
        log.debug("Graphics protocol: sixel")
        return "sixel"
    log.debug("Graphics protocol: none")
    return None


def _quantize_colors(
    colors: np.ndarray,
    n_colors: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Quantize RGB colors to a uniform cube palette.

    Picks the largest ``levels`` such that ``levels ** 3 <= n_colors`` and
    ``levels >= 2``. Clamped to 6 (216 colors, the practical DEC sixel ceiling).

    :param colors: Array of shape ``(H, W, 3)`` with float values 0.0..1.0.
    :param n_colors: Maximum palette size.
    :returns: ``(indexed, palette)`` tuple where *indexed* is ``(H, W)`` uint8
        and *palette* is ``(N, 3)`` float32.
    """
    levels = 2
    while (levels + 1) ** 3 <= n_colors and levels < 6:
        levels += 1

    r = (colors[:, :, 0] * (levels - 0.001)).astype(np.uint8)
    g = (colors[:, :, 1] * (levels - 0.001)).astype(np.uint8)
    b = (colors[:, :, 2] * (levels - 0.001)).astype(np.uint8)
    indices = r * levels * levels + g * levels + b

    n_actual = levels ** 3
    palette = np.zeros((n_actual, 3), dtype=np.float32)
    for i in range(n_actual):
        ri = i // (levels * levels)
        gi = (i // levels) % levels
        bi = i % levels
        palette[i] = [(ri + 0.5) / levels, (gi + 0.5) / levels, (bi + 0.5) / levels]

    return indices.astype(np.uint8), palette


def encode_sixel(
    colors: np.ndarray,
    dest: io.TextIOBase,
    max_colors: int = 256,
    scale: int = 1,
) -> None:
    """Encode an RGB image as a sixel escape sequence and write to *dest*.

    :param colors: Array of shape ``(H, W, 3)`` with float values 0.0..1.0.
    :param dest: Text stream to write the escape sequence into.
    :param max_colors: Maximum palette size (default 256).
    :param scale: Integer pixel scale factor.  Each sixel pixel maps to
        *scale* screen pixels.  Use this to make the image fill the
        terminal when the font cell size is larger than the glyph pixels.
    """
    h, w = colors.shape[:2]

    if scale > 1:
        colors = np.repeat(np.repeat(colors, scale, axis=0), scale, axis=1)
        h, w = colors.shape[:2]

    pad_h = (6 - h % 6) % 6
    if pad_h > 0:
        colors = np.pad(colors, ((0, pad_h), (0, 0), (0, 0)), constant_values=0)
        h = colors.shape[0]

    indices, palette = _quantize_colors(colors, max_colors)

    dest.write(DCS_START)

    for i, (r, g, b) in enumerate(palette):
        dest.write(f"#{i};2;{int(r * 100)};{int(g * 100)};{int(b * 100)}")

    for band_y in range(0, h, 6):
        band = indices[band_y : band_y + 6, :]  # (6, W) uint8
        present = np.unique(band)
        for color_idx in present:
            mask = band == color_idx  # (6, W) bool
            # Pack 6 rows into a single uint8 per column via dot product.
            bits = np.array([1, 2, 4, 8, 16, 32], dtype=np.uint8)
            patterns = np.dot(mask.T.astype(np.uint8), bits).astype(np.uint8)
            dest.write(f"#{color_idx}")
            # Run-length encode using numpy diff to find boundaries.
            if w == 0:
                continue
            changes = np.diff(patterns, prepend=np.uint8(~patterns[0]))
            run_starts = np.where(changes != 0)[0]
            run_lengths = np.diff(np.append(run_starts, w))
            for start, length in zip(run_starts, run_lengths):
                pat = int(patterns[start])
                char = chr(0x3F + pat)
                if length > 3:
                    dest.write(f"!{length}{char}")
                else:
                    dest.write(char * length)
            dest.write("$")
        dest.write("-")

    dest.write(DCS_END)


def _make_png(colors: np.ndarray) -> bytes:
    """Create a minimal PNG from an RGB image without external dependencies.

    Uses raw DEFLATE compression via :mod:`zlib`.

    :param colors: Array of shape ``(H, W, 3)`` with float values 0.0..1.0.
    :returns: Valid PNG file bytes.
    """
    h, w = colors.shape[:2]
    rgb = (colors * 255).astype(np.uint8)

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(rgb[y].tobytes())

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        length = len(data).to_bytes(4, "big")
        chunk_data = chunk_type + data
        crc = zlib.crc32(chunk_data) & 0xFFFFFFFF
        return length + chunk_data + crc.to_bytes(4, "big")

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    ihdr = _chunk(b"IHDR", ihdr_data)
    compressed = zlib.compress(bytes(raw), level=6)
    idat = _chunk(b"IDAT", compressed)
    iend = _chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


def _try_pil_png(colors: np.ndarray) -> bytes | None:
    """Try to create a PNG using PIL, which compresses better.

    :param colors: Array of shape ``(H, W, 3)`` with float values 0.0..1.0.
    :returns: PNG bytes, or ``None`` if PIL is not installed.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    rgb = (colors * 255).astype(np.uint8)
    img = Image.fromarray(rgb)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def encode_kitty(
    colors: np.ndarray,
    dest: io.TextIOBase,
    fmt: str = "png",
    columns: int = 0,
    rows: int = 0,
) -> None:
    """Encode an RGB image as a Kitty graphics escape sequence and write to *dest*.

    :param colors: Array of shape ``(H, W, 3)`` with float values 0.0..1.0.
    :param dest: Text stream to write the escape sequence into.
    :param fmt: Output format: ``"png"`` (default), ``"rgb"``, or ``"rgba"``.
    :param columns: Display width in terminal columns.  When > 0, the
        terminal scales the image to fit *columns* cells, providing
        font-size-independent sizing.
    :param rows: Display height in terminal rows.  When > 0, the
        terminal scales the image to fit *rows* cells.
    """
    h, w = colors.shape[:2]

    if fmt == "png":
        data = _try_pil_png(colors)
        if data is None:
            data = _make_png(colors)
        fmt_code = 100
        params = f"a=T,C=1,f={fmt_code}"
    else:
        rgb = (colors * 255).astype(np.uint8)
        if fmt == "rgba":
            alpha = np.full((h, w, 1), 255, dtype=np.uint8)
            rgba = np.concatenate([rgb, alpha], axis=2)
            data = rgba.tobytes()
            fmt_code = 32
        else:
            data = rgb.tobytes()
            fmt_code = 24
        data = zlib.compress(data, level=6)
        params = f"a=T,C=1,f={fmt_code},o=z,s={w},v={h}"

    if columns > 0:
        params += f",c={columns}"
    if rows > 0:
        params += f",r={rows}"

    b64_data = base64.b64encode(data).decode("ascii")

    first_chunk = True
    offset = 0
    while offset < len(b64_data):
        chunk = b64_data[offset : offset + MAX_CHUNK_SIZE]
        offset += MAX_CHUNK_SIZE
        more = 1 if offset < len(b64_data) else 0
        if first_chunk:
            dest.write(f"{APC_START}{params},m={more};{chunk}{APC_END}")
            first_chunk = False
        else:
            dest.write(f"{APC_START}m={more};{chunk}{APC_END}")
