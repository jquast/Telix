"""Telix-specific CLI configuration threaded through the call chain to client shells."""

from __future__ import annotations

import typing
import dataclasses


@dataclasses.dataclass
class TelixConfig:
    """Telix CLI options consumed by client shell setup functions."""

    colormatch: str = "vga"
    color_brightness: float = 1.0
    color_contrast: float = 1.0
    background_color: str = "#000000"
    no_ice_colors: bool = False
    ansi_keys: bool = False
    no_repl: bool = False
    echo_mode: str = "auto"
    clear_homes_cursor: bool = False
    ff_clears_screen: bool = False
    graphics_font: str = ""
    graphics_columns: int | None = None
    graphics_rows: int | None = None
    font_id: int | None = None

    _DEFAULTS: typing.ClassVar[dict[str, object]] = {
        "colormatch": "vga",
        "color_brightness": 1.0,
        "color_contrast": 1.0,
        "background_color": "#000000",
        "no_ice_colors": False,
        "ansi_keys": False,
        "no_repl": False,
        "echo_mode": "auto",
        "clear_homes_cursor": False,
        "ff_clears_screen": False,
        "graphics_font": "",
        "graphics_columns": None,
        "graphics_rows": None,
        "font_id": None,
    }

    @classmethod
    def from_args(cls, args: typing.Any, **overrides: typing.Any) -> TelixConfig:
        """Build a TelixConfig from a parsed argparse Namespace, applying protocol-specific overrides."""
        kwargs = {name: getattr(args, name, default) for name, default in cls._DEFAULTS.items()}
        kwargs.update(overrides)
        return cls(**kwargs)
