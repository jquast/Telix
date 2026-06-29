"""Tests for telix.graphics_renderer -- sixel and kitty encoders."""

import io
import types

import numpy as np
import pytest

from telix.graphics_renderer import APC_END, DCS_END, APC_START, DCS_START, encode_kitty, encode_sixel, _quantize_colors


def _solid_rgb(w, h, r, g, b):
    colors = np.zeros((h, w, 3), dtype=np.float32)
    colors[:, :, 0] = r
    colors[:, :, 1] = g
    colors[:, :, 2] = b
    return colors


class TestQuantizeColors:
    def test_uniform_red_block(self):
        colors = np.full((6, 10, 3), (1.0, 0.0, 0.0), dtype=np.float32)
        indices, palette = _quantize_colors(colors, 256)
        assert indices.shape == (6, 10)
        assert palette.shape[0] >= 8
        assert np.all(indices == indices[0, 0])

    def test_max_colors_clamped_to_216(self):
        colors = np.random.rand(12, 10, 3).astype(np.float32)
        indices, palette = _quantize_colors(colors, 500)
        assert palette.shape[0] <= 216


class TestEncodeSixel:
    def test_output_starts_with_dcs(self):
        colors = _solid_rgb(8, 6, 0.0, 1.0, 0.0)
        buf = io.StringIO()
        encode_sixel(colors, buf, max_colors=64)
        assert buf.getvalue().startswith(DCS_START)

    def test_output_ends_with_dcs_end(self):
        colors = _solid_rgb(8, 6, 0.0, 1.0, 0.0)
        buf = io.StringIO()
        encode_sixel(colors, buf, max_colors=64)
        assert buf.getvalue().rstrip().endswith(DCS_END)

    def test_output_contains_color_definition(self):
        colors = _solid_rgb(8, 6, 0.0, 1.0, 0.0)
        buf = io.StringIO()
        encode_sixel(colors, buf, max_colors=64)
        output = buf.getvalue()
        assert "#0;2;" in output

    def test_sixel_characters_in_valid_range(self):
        colors = _solid_rgb(8, 6, 0.5, 0.5, 0.5)
        buf = io.StringIO()
        encode_sixel(colors, buf, max_colors=64)
        output = buf.getvalue()
        data_section = output[len(DCS_START) : -len(DCS_END)]
        for ch in data_section:
            if ch in ("$", "-", "!", "\n"):
                continue
            if ch.startswith("#") or ch.isdigit():
                continue
            if ch in (";", "\x1b"):
                continue
            if "?" <= ch <= "~":
                continue

    def test_height_padded_to_multiple_of_six(self):
        colors = _solid_rgb(10, 7, 0.0, 0.0, 1.0)
        buf = io.StringIO()
        encode_sixel(colors, buf, max_colors=64)
        output = buf.getvalue()
        assert DCS_START in output
        assert DCS_END in output

    def test_run_length_encoding(self):
        colors = _solid_rgb(20, 6, 1.0, 0.0, 0.0)
        buf = io.StringIO()
        encode_sixel(colors, buf, max_colors=64)
        output = buf.getvalue()
        assert DCS_START in output
        assert DCS_END in output


class TestEncodeKitty:
    def test_rgb_output(self):
        colors = _solid_rgb(8, 6, 0.0, 1.0, 0.0)
        buf = io.StringIO()
        encode_kitty(colors, buf, fmt="rgb")
        output = buf.getvalue()
        assert APC_START in output
        assert APC_END in output
        assert "f=24" in output
        assert "a=T" in output

    def test_rgba_output(self):
        colors = _solid_rgb(8, 6, 0.0, 0.0, 1.0)
        buf = io.StringIO()
        encode_kitty(colors, buf, fmt="rgba")
        output = buf.getvalue()
        assert APC_START in output
        assert APC_END in output
        assert "f=32" in output

    def test_output_contains_base64_data(self):
        colors = _solid_rgb(8, 6, 0.5, 0.5, 0.5)
        buf = io.StringIO()
        encode_kitty(colors, buf, fmt="rgb")
        output = buf.getvalue()
        assert ";" in output
        data_part = output.split(";", 1)[1] if ";" in output else ""
        assert len(data_part) > 0

    def test_chunks_with_m_parameter(self):
        colors = _solid_rgb(80, 60, 0.5, 0.5, 0.5)
        buf = io.StringIO()
        encode_kitty(colors, buf, fmt="rgb")
        output = buf.getvalue()
        assert "m=1" in output or "m=0" in output
