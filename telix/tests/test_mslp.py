"""Tests for MSLP link parsing and collection."""

import pytest

from telix import mslp


class TestMslpCollectorFilter:
    def test_strips_osc68_with_bel(self):
        collector = mslp.MslpCollector()
        result = collector.filter("\x1b]68;1;Look;look\x07\x1b[4mlook\x1b[24m")
        assert result == "\x1b[4mlook\x1b[24m"

    def test_strips_osc68_with_st(self):
        collector = mslp.MslpCollector()
        result = collector.filter("\x1b]68;1;Look;look\x1b\\\x1b[4mlook\x1b[24m")
        assert result == "\x1b[4mlook\x1b[24m"

    def test_collects_type1_command(self):
        collector = mslp.MslpCollector()
        collector.filter("\x1b]68;1;Look;look\x07")
        assert len(collector.pending) == 1
        assert collector.pending[0].command == "look"
        assert collector.pending[0].label == "Look"

    def test_strips_type2_without_collecting(self):
        collector = mslp.MslpCollector()
        result = collector.filter("\x1b]68;2;Secret;hidden\x07visible")
        assert result == "visible"
        assert collector.pending == []

    def test_multiple_links_in_one_chunk(self):
        collector = mslp.MslpCollector()
        text = "\x1b]68;1;;look\x07 \x1b]68;1;;north\x07 \x1b]68;1;;south\x07"
        result = collector.filter(text)
        assert result == "  "
        assert len(collector.pending) == 3
        assert [link.command for link in collector.pending] == ["look", "north", "south"]

    def test_empty_label(self):
        collector = mslp.MslpCollector()
        collector.filter("\x1b]68;1;;look\x07")
        assert collector.pending[0].label == ""
        assert collector.pending[0].command == "look"

    def test_no_osc68_passthrough(self):
        collector = mslp.MslpCollector()
        result = collector.filter("plain text with \x1b[1mbold\x1b[0m")
        assert result == "plain text with \x1b[1mbold\x1b[0m"
        assert collector.pending == []

    def test_empty_string(self):
        collector = mslp.MslpCollector()
        assert collector.filter("") == ""


class TestMslpCollectorPrompt:
    def test_on_prompt_promotes_pending(self):
        collector = mslp.MslpCollector()
        collector.filter("\x1b]68;1;;look\x07\x1b]68;1;;north\x07")
        collector.on_prompt()
        assert collector.count == 2
        assert collector.pending == []
        assert collector.available[0].command == "look"
        assert collector.available[1].command == "north"

    def test_on_prompt_no_pending_keeps_available(self):
        collector = mslp.MslpCollector()
        collector.filter("\x1b]68;1;;look\x07")
        collector.on_prompt()
        collector.on_prompt()
        assert collector.count == 1

    def test_new_round_replaces_available(self):
        collector = mslp.MslpCollector()
        collector.filter("\x1b]68;1;;look\x07")
        collector.on_prompt()
        collector.filter("\x1b]68;1;;east\x07\x1b]68;1;;west\x07")
        collector.on_prompt()
        assert collector.count == 2
        assert collector.available[0].command == "east"

    def test_count_zero_initially(self):
        collector = mslp.MslpCollector()
        assert collector.count == 0


class TestMslpCollectorEdgeCases:
    def test_semicolons_in_label(self):
        collector = mslp.MslpCollector()
        collector.filter("\x1b]68;1;Go;go north\x07")
        assert collector.pending[0].command == "go north"

    @pytest.mark.parametrize(
        "text, expected_clean",
        [
            ("\x1b]68;1;;cmd\x07before", "before"),
            ("before\x1b]68;1;;cmd\x07after", "beforeafter"),
            ("before\x1b]68;1;;cmd\x07", "before"),
        ],
    )
    def test_position_variants(self, text, expected_clean):
        collector = mslp.MslpCollector()
        assert collector.filter(text) == expected_clean

    def test_mixed_types(self):
        collector = mslp.MslpCollector()
        text = "\x1b]68;1;;look\x07\x1b]68;2;;secret\x07\x1b]68;1;;north\x07"
        collector.filter(text)
        assert len(collector.pending) == 2
        assert collector.pending[0].command == "look"
        assert collector.pending[1].command == "north"
