"""Tests for telix.util module."""

from __future__ import annotations

# std imports
import datetime

# local
import pytest
from telix.util import relative_time, erase_eol


def test_relative_time_empty():
    assert relative_time("") == ""


def test_relative_time_recent():
    now = datetime.datetime.now(datetime.timezone.utc)
    iso = now.isoformat()
    result = relative_time(iso)
    assert result.endswith("s ago")


def test_relative_time_hours():
    then = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
    result = relative_time(then.isoformat())
    assert result == "3h ago"


def test_relative_time_days():
    then = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)
    result = relative_time(then.isoformat())
    assert result == "5d ago"


def test_relative_time_invalid():
    result = relative_time("not-a-date")
    assert result == "not-a-date"[:10]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("line\r\n", "line\x1b[K\r\n"),
        ("line\r\r\n", "line\x1b[K\r\r\n"),
        ("line\r\nmore", "line\x1b[K\r\nmore"),
        ("\x1b[2H\x1b[0;1;40;36m\r\n", "\x1b[2H\x1b[0;1;40;36m\r\n"),
        ("\x1b[2H\x1b[0;1;40;36m\r\nline", "\x1b[2H\x1b[0;1;40;36m\r\nline"),
        ("no newline", "no newline"),
        ("", ""),
    ],
)
def test_erase_eol(text, expected):
    """erase_eol inserts \\x1b[K before \\r+\\n only on lines with visible content."""
    assert erase_eol(text) == expected
