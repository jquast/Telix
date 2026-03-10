"""Sextant block character table and password scrambling."""

# std imports
import random

# Each sextant character (U+1FB00-U+1FB3B) encodes a 2x3 pixel grid.
# Bits 0-5 map to top-left, top-right, mid-left, mid-right, bot-left, bot-right.
# We build a 64-entry lookup: index = bitmask -> Unicode character.
SEXTANT: list[str] = [" "] * 64
SEXTANT[63] = "\u2588"  # FULL BLOCK
for b in range(1, 63):
    u = sum((1 << i) for i in range(6) if b & (1 << (5 - i)))
    SEXTANT[b] = (
        "\u258c" if u == 21 else "\u2590" if u == 42 else chr(0x1FB00 + u - 1 - sum(1 for x in (21, 42) if x < u))
    )
del b, u

#: Non-space sextant characters for password scrambling.
SEXTANT_VISIBLE = SEXTANT[1:]

SCRAMBLE_LEN = 17


def scramble_password(length: int = SCRAMBLE_LEN) -> str:
    """Return *length* random sextant block characters."""
    return "".join(random.choice(SEXTANT_VISIBLE) for _ in range(length))
