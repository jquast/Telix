"""Small shared utility functions."""

# std imports
import os
import re
import json
import typing
import logging
import datetime

from . import paths

ANSI_ESC_RE = re.compile(r"\x1b\[[^@-~]*[@-~]|\x1b.")
DECSTBM_RE = re.compile(r"\x1b\[\d*;?\d*r")


def strip_decstbm(text: str) -> str:
    r"""
    Remove DECSTBM (Set Scrolling Region) escape sequences from server output.

    Some MUD servers send ``\x1b[Pt;Pbr`` to set their own scroll region,
    which conflicts with the client's managed scroll region and causes the
    toolbar to scroll into the text area.

    :param text: Decoded server text.
    :returns: Text with DECSTBM sequences removed.
    """
    result = DECSTBM_RE.sub("", text)
    if len(result) != len(text):
        logging.getLogger(__name__).debug("stripped DECSTBM scroll region sequence from server output")
    return result


def erase_eol(text: str) -> str:
    r"""
    Insert ``\x1b[K`` (erase to EOL) before ``\r+\n`` on lines with visible content.

    Inserts an erase-to-EOL sequence immediately before each carriage-return +
    line-feed group, but only when the segment since the previous line ending
    contains at least one printable (non-escape-sequence) character.

    Lines that contain only cursor-positioning or attribute sequences (e.g.
    ``\x1b[2H\x1b[...m\r\n``) are left unchanged so that absolute cursor
    moves do not cause the destination row to be erased.

    :param text: Decoded server text, possibly containing ANSI escape sequences.
    :returns: Text with erase-to-EOL sequences inserted before line endings on
        lines that contain at least one printable character.
    """
    parts = re.split(r"(\r+\n)", text)
    result = []
    for i in range(0, len(parts), 2):
        seg = parts[i]
        if i + 1 < len(parts):
            if ANSI_ESC_RE.sub("", seg):
                result.append(seg + "\x1b[K" + parts[i + 1])
            else:
                result.append(seg + parts[i + 1])
        else:
            result.append(seg)
    return "".join(result)


def load_json_entries(path: str, session_key: str, entry_key: str) -> list[dict[str, typing.Any]]:
    """
    Load a list of entries from a session-keyed JSON file.

    The file is structured as ``{session_key: {entry_key: [...]}, ...}``.
    Returns an empty list when the session or key is absent.

    :param path: Path to the JSON file.
    :param session_key: Session identifier (e.g. ``"host:port"``).
    :param entry_key: Key within the session object (e.g. ``"triggers"``).
    :returns: List of raw entry dicts.
    :raises FileNotFoundError: When *path* does not exist.
    :raises json.JSONDecodeError: When the file is not valid JSON.
    """
    with open(path, encoding="utf-8") as fh:
        data: dict[str, typing.Any] = json.load(fh)
    session_data: dict[str, typing.Any] = data.get(session_key, {})
    entries: list[dict[str, typing.Any]] = session_data.get(entry_key, [])
    return entries


def save_json_entries(path: str, session_key: str, entry_key: str, entries: list[dict[str, typing.Any]]) -> None:
    """
    Atomically save *entries* to *path* under ``session_key[entry_key]``.

    Reads existing data from *path* (if present) so that other sessions'
    data is preserved.  Writes atomically via :func:`~telix.paths.atomic_write`.

    :param path: Path to the JSON file.
    :param session_key: Session identifier (e.g. ``"host:port"``).
    :param entry_key: Key within the session object (e.g. ``"triggers"``).
    :param entries: Serialised entry dicts to store.
    """
    data: dict[str, typing.Any] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    data[session_key] = {entry_key: entries}
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    paths.atomic_write(path, content)


def relative_time(iso_str: str) -> str:
    """Format an ISO timestamp as a relative time like ``'2h ago'``."""
    if not iso_str:
        return ""
    try:
        then = datetime.datetime.fromisoformat(iso_str)
        if then.tzinfo is None:
            now = datetime.datetime.now()
        else:
            now = datetime.datetime.now(datetime.timezone.utc)
        delta = now - then
        seconds = int(delta.total_seconds())
        if seconds < 0:
            return ""
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except (ValueError, TypeError):
        return iso_str[:10]
