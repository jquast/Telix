"""Tests for telix.raw_client -- argument parsing and data delivery."""

import asyncio

import pytest

from telix.raw_client import build_parser


class TestBuildParser:
    def test_returns_parser(self):
        parser = build_parser()
        assert parser.prog == "telix"

    def test_parses_host(self):
        args = build_parser().parse_args(["example.com"])
        assert args.host == "example.com"
        assert args.port == 23

    def test_default_port_is_23(self):
        args = build_parser().parse_args(["host"])
        assert args.port == 23

    def test_parses_port(self):
        args = build_parser().parse_args(["host", "--port", "5200"])
        assert args.port == 5200

    def test_parses_encoding(self):
        args = build_parser().parse_args(["host", "--encoding", "cp437"])
        assert args.encoding == "cp437"

    def test_default_encoding_is_utf8(self):
        args = build_parser().parse_args(["host"])
        assert args.encoding == "utf-8"

    def test_parses_encoding_errors(self):
        args = build_parser().parse_args(["host", "--encoding-errors", "ignore"])
        assert args.encoding_errors == "ignore"

    def test_default_encoding_errors_is_replace(self):
        args = build_parser().parse_args(["host"])
        assert args.encoding_errors == "replace"

    def test_parses_loglevel(self):
        args = build_parser().parse_args(["host", "--loglevel", "debug"])
        assert args.loglevel == "debug"

    def test_default_loglevel_is_warn(self):
        args = build_parser().parse_args(["host"])
        assert args.loglevel == "warn"

    def test_parses_logfile(self):
        args = build_parser().parse_args(["host", "--logfile", "/tmp/telix.log"])
        assert args.logfile == "/tmp/telix.log"

    def test_parses_typescript(self):
        args = build_parser().parse_args(["host", "--typescript", "session.txt"])
        assert args.typescript == "session.txt"

    def test_parses_colormatch(self):
        args = build_parser().parse_args(["host", "--colormatch", "cga"])
        assert args.colormatch == "cga"

    def test_default_colormatch_is_vga(self):
        args = build_parser().parse_args(["host"])
        assert args.colormatch == "vga"

    def test_no_ice_colors_default_false(self):
        args = build_parser().parse_args(["host"])
        assert args.no_ice_colors is False

    def test_parses_no_ice_colors(self):
        args = build_parser().parse_args(["host", "--no-ice-colors"])
        assert args.no_ice_colors is True

    def test_parses_ansi_keys(self):
        args = build_parser().parse_args(["host", "--ansi-keys"])
        assert args.ansi_keys is True

    def test_default_ansi_keys_is_false(self):
        args = build_parser().parse_args(["host"])
        assert args.ansi_keys is False

    def test_parses_local_echo(self):
        args = build_parser().parse_args(["host", "--local-echo"])
        assert args.local_echo is True

    def test_parses_remote_echo(self):
        args = build_parser().parse_args(["host", "--remote-echo"])
        assert args.remote_echo is True

    def test_parses_color_brightness(self):
        args = build_parser().parse_args(["host", "--color-brightness", "1.5"])
        assert args.color_brightness == 1.5

    def test_parses_color_contrast(self):
        args = build_parser().parse_args(["host", "--color-contrast", "0.8"])
        assert args.color_contrast == 0.8

    def test_parses_background_color(self):
        args = build_parser().parse_args(["host", "--background-color", "#123456"])
        assert args.background_color == "#123456"

    def test_parses_clear_homes_cursor(self):
        args = build_parser().parse_args(["host", "--clear-homes-cursor"])
        assert args.clear_homes_cursor is True

    def test_parses_ff_clears_screen(self):
        args = build_parser().parse_args(["host", "--ff-clears-screen"])
        assert args.ff_clears_screen is True

    def test_parses_graphics_font_auto(self):
        args = build_parser().parse_args(["host", "--graphics-font"])
        assert args.graphics_font == "auto"

    def test_parses_graphics_columns(self):
        args = build_parser().parse_args(["host", "--graphics-columns", "80"])
        assert args.graphics_columns == 80

    def test_parses_graphics_rows(self):
        args = build_parser().parse_args(["host", "--graphics-rows", "25"])
        assert args.graphics_rows == 25

    def test_parses_font_id(self):
        args = build_parser().parse_args(["host", "--font-id", "3"])
        assert args.font_id == 3

    def test_parser_exposes_all_color_args_attributes(self):
        args = build_parser().parse_args(["localhost"])
        required = [
            "colormatch",
            "color_brightness",
            "color_contrast",
            "background_color",
            "no_ice_colors",
            "ansi_keys",
            "clear_homes_cursor",
            "ff_clears_screen",
            "graphics_font",
            "graphics_columns",
            "graphics_rows",
            "font_id",
        ]
        missing = [a for a in required if not hasattr(args, a)]
        assert not missing, f"parser is missing attributes: {missing}"
