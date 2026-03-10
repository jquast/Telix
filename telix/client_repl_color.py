"""HSV/RGB colour math and vital-bar flash animation helpers."""

# std imports
import colorsys
import dataclasses


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    """Convert HSV (h in [0,360), s/v in [0,1]) to (r, g, b) in [0,255]."""
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def rgb_to_hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert (r, g, b) in [0,255] to HSV (h in [0,360), s/v in [0,1])."""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return (h * 360.0, s, v)


def lerp_hsv(
    hsv1: tuple[float, float, float], hsv2: tuple[float, float, float], t: float
) -> tuple[float, float, float]:
    """Linearly interpolate between two HSV colors using shortest-arc hue."""
    h1, s1, v1 = hsv1
    h2, s2, v2 = hsv2
    dh = (h2 - h1) % 360.0
    if dh > 180.0:
        dh -= 360.0
    h = (h1 + t * dh) % 360.0
    return (h, s1 + t * (s2 - s1), v1 + t * (v2 - v1))


@dataclasses.dataclass(frozen=True)
class FlashTiming:
    """Vital-bar flash animation timing constants."""

    RAMP_UP: float = 0.100
    HOLD: float = 0.200
    RAMP_DOWN: float = 0.350
    INTERVAL: float = 0.033

    @property
    def DURATION(self) -> float:  # noqa: N802
        """Total flash duration (ramp_up + hold + ramp_down)."""
        return self.RAMP_UP + self.HOLD + self.RAMP_DOWN


FLASH = FlashTiming()


def flash_color(base_hex: str, elapsed: float) -> str:
    """
    Compute the flash-animated color for *base_hex* at *elapsed* seconds.

    :param base_hex: Original ``#rrggbb`` hex color.
    :param elapsed: Seconds since flash started; negative means no flash.
    :returns: Interpolated ``#rrggbb`` hex color.
    """
    if elapsed < 0.0 or elapsed >= FLASH.DURATION:
        return base_hex
    r = int(base_hex[1:3], 16)
    g = int(base_hex[3:5], 16)
    b = int(base_hex[5:7], 16)
    hsv_orig = rgb_to_hsv(r, g, b)
    # Flash toward white (same hue, zero saturation, full brightness)
    # to avoid hue-interpolation artifacts (e.g. green->magenta goes through cyan).
    hsv_inv = (hsv_orig[0], 0.0, 1.0)
    if elapsed < FLASH.RAMP_UP:
        t = elapsed / FLASH.RAMP_UP
    elif elapsed < FLASH.RAMP_UP + FLASH.HOLD:
        t = 1.0
    else:
        t = (FLASH.DURATION - elapsed) / FLASH.RAMP_DOWN
    h, s, v = lerp_hsv(hsv_orig, hsv_inv, t)
    cr, cg, cb = hsv_to_rgb(h, s, v)
    return f"#{cr:02x}{cg:02x}{cb:02x}"
