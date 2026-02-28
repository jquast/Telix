"""Load the bundled MUD/BBS directory and convert to session configs."""

from __future__ import annotations

# std imports
import json
from typing import Any
from importlib import resources


def load_directory() -> list[dict[str, Any]]:
    """
    Read the bundled ``telix/data/directory.json``.

    :returns: list of directory entry dicts
    """
    ref = resources.files("telix.data").joinpath("directory.json")
    text = ref.read_text(encoding="utf-8")
    result: list[dict[str, Any]] = json.loads(text)
    return result


def directory_to_sessions() -> dict[str, Any]:
    """
    Convert directory entries to a sessions dict.

    Each entry becomes a :class:`~telix.client_tui.SessionConfig` keyed by
    ``"host:port"``.  Only fields that differ from ``SessionConfig`` defaults
    are set.

    :returns: dict mapping ``"host:port"`` to ``SessionConfig``
    """
    from .client_tui import SessionConfig

    entries = load_directory()
    sessions: dict[str, Any] = {}
    for entry in entries:
        host = entry["host"]
        port = entry.get("port", 23)
        key = f"{host}:{port}"
        cfg = SessionConfig(
            host=host,
            port=port,
            name=entry.get("name", host),
        )
        if entry.get("ssl"):
            cfg.ssl = True
        enc = entry.get("encoding")
        if enc:
            cfg.encoding = enc
        sessions[key] = cfg

    if "1984.ws:23" in sessions:
        sessions["1984.ws:23"].bookmarked = True
    return sessions
