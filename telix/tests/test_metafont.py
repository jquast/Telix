"""Tests for telix.metafont -- octant bitmap font rendering."""

import pytest

from telix.metafont import (
    OCTANT,
    CELLS_PER_CHAR_X,
    CELLS_PER_CHAR_Y,
    BitmapFont,
    glyph_to_octants,
    load_font,
    render_cell,
)
from telix.fonts.font_registry import (
    FONT_TABLE,
    FONT_BY_ID,
    FONT_BY_SHORT_NAME,
    FONT_BYTES,
    GLYPH_HEIGHT,
    FontEntry,
)


class TestOctantTable:

    def test_table_length(self):
        assert len(OCTANT) == 256

    def test_all_entries_are_single_chars(self):
        for i, ch in enumerate(OCTANT):
            assert len(ch) == 1

    def test_empty_pattern(self):
        assert OCTANT[0x00] == "\u00a0"

    def test_full_pattern(self):
        assert OCTANT[0xFF] == "\u2588"

    def test_left_half(self):
        assert OCTANT[0x55] == "\u258c"

    def test_right_half(self):
        assert OCTANT[0xAA] == "\u2590"

    def test_upper_half(self):
        assert OCTANT[0x0F] == "\u2580"

    def test_lower_half(self):
        assert OCTANT[0xF0] == "\u2584"

    def test_all_unique(self):
        assert len(set(OCTANT)) == 256


class TestFontRegistry:

    def test_45_fonts(self):
        assert len(FONT_TABLE) == 45

    def test_font_ids_sequential(self):
        ids = [e.font_id for e in FONT_TABLE]
        assert ids == list(range(45))

    def test_by_id_matches_table(self):
        for entry in FONT_TABLE:
            assert FONT_BY_ID[entry.font_id] is entry

    def test_short_names_unique(self):
        names = [e.short_name for e in FONT_TABLE]
        assert len(set(names)) == len(names)

    def test_by_short_name_matches(self):
        for entry in FONT_TABLE:
            assert FONT_BY_SHORT_NAME[entry.short_name] is entry

    def test_cp437_is_default(self):
        assert FONT_BY_ID[0].short_name == "cp437"
        assert FONT_BY_ID[0].encoding == "cp437"

    def test_topaz_encoding_is_latin1(self):
        topaz = FONT_BY_SHORT_NAME["topaz"]
        assert topaz.encoding == "iso-8859-1"

    def test_bin_offsets_non_overlapping(self):
        offsets = sorted(e.bin_offset for e in FONT_TABLE)
        for i in range(1, len(offsets)):
            assert offsets[i] >= offsets[i - 1] + FONT_BYTES


class TestBitmapFont:

    @pytest.fixture()
    def cp437(self):
        return load_font(0)

    def test_load_cp437(self, cp437):
        assert cp437.font_id == 0
        assert cp437.encoding == "cp437"
        assert len(cp437.data) == FONT_BYTES

    def test_glyph_length(self, cp437):
        g = cp437.glyph(0x41)
        assert len(g) == GLYPH_HEIGHT

    def test_space_glyph_is_empty(self, cp437):
        g = cp437.glyph(0x20)
        assert all(row == 0 for row in g)

    def test_full_block_glyph_nonzero(self, cp437):
        g = cp437.glyph(0xDB)
        assert any(row != 0 for row in g)

    def test_caching(self):
        f1 = load_font(0)
        f2 = load_font(0)
        assert f1 is f2

    @pytest.mark.parametrize("font_id", range(45))
    def test_all_fonts_loadable(self, font_id):
        f = load_font(font_id)
        assert len(f.data) == FONT_BYTES

    def test_unknown_font_id_raises(self):
        with pytest.raises(KeyError):
            load_font(999)


class TestGlyphToOctants:

    def test_empty_glyph(self):
        bitmap = [0] * 16
        grid = glyph_to_octants(bitmap)
        assert len(grid) == CELLS_PER_CHAR_Y
        assert all(len(row) == CELLS_PER_CHAR_X for row in grid)
        assert all(p == 0 for row in grid for p in row)

    def test_full_glyph(self):
        bitmap = [0xFF] * 16
        grid = glyph_to_octants(bitmap)
        assert all(p == 0xFF for row in grid for p in row)

    def test_grid_dimensions(self):
        bitmap = [0x55] * 16
        grid = glyph_to_octants(bitmap)
        assert len(grid) == 4
        assert all(len(row) == 4 for row in grid)

    def test_left_half_pattern(self):
        bitmap = [0xC0] * 16
        grid = glyph_to_octants(bitmap)
        for row in grid:
            assert row[0] == 0xFF
            assert row[1] == 0x00
            assert row[2] == 0x00
            assert row[3] == 0x00

    def test_single_pixel_top_left(self):
        bitmap = [0x80] + [0] * 15
        grid = glyph_to_octants(bitmap)
        assert grid[0][0] == 0x01
        assert grid[0][1] == 0
        assert grid[1][0] == 0

    def test_values_in_range(self):
        font = load_font(0)
        for code in range(256):
            bitmap = font.glyph(code)
            grid = glyph_to_octants(bitmap)
            for row in grid:
                for p in row:
                    assert 0 <= p <= 255


class TestRenderCell:

    def test_returns_4_lines(self):
        font = load_font(0)
        lines = render_cell(0x41, (255, 255, 255), (0, 0, 0), font)
        assert len(lines) == CELLS_PER_CHAR_Y

    def test_sgr_present(self):
        font = load_font(0)
        lines = render_cell(0x41, (170, 170, 170), (0, 0, 170), font)
        for line in lines:
            assert "\033[38;2;170;170;170;48;2;0;0;170m" in line

    def test_each_line_has_4_visible_chars(self):
        font = load_font(0)
        lines = render_cell(0x20, (255, 255, 255), (0, 0, 0), font)
        for line in lines:
            sgr_end = line.index("m") + 1
            visible = line[sgr_end:]
            assert len(visible) == CELLS_PER_CHAR_X

    def test_space_renders_empty_octants(self):
        font = load_font(0)
        lines = render_cell(0x20, (255, 255, 255), (0, 0, 0), font)
        for line in lines:
            assert OCTANT[0x00] * 4 in line
