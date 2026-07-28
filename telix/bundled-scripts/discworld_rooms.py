"""
Parse Discworld Room.Writtenmap to discover exits from the current room.

Discworld sends exit information as ``Room.Writtenmap`` instead of ``Room.Info``.  The prose
describes nearby rooms using an ``of N D`` pattern, e.g.::

    Exits south and north of one west and the limit of your vision is
    one west from here.
    doors east and northeast of one north, an exit southwest of one
    north, exits south, southeast and west of two south.

When a room at ``N D`` is described, the current room has an exit in
direction ``D``.  This script registers a callback that fires
synchronously whenever ``Room.Writtenmap`` arrives, so exits are
populated in the room graph before autodiscover or randomwalk reads
them.

On first run the script also downloads Quow's Discworld map database
(https://quow.co.uk/maps/_quowmap_database.db) and imports all
known rooms and exits into the local room graph, giving instant
access to 18,000+ pre-mapped rooms.

Start it as a background daemon at the beginning of a session::

    `async discworld_rooms`
"""

import os
import re
import asyncio
import logging
import urllib.request

from telix.paths import xdg_data_dir
from telix.scripts import ScriptContext

log = logging.getLogger(__name__)

_QUOWMAP_URL = "https://quow.co.uk/maps/_quowmap_database.db"

_NUMBER_WORDS = {
    "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
}

_DIRECTIONS = [
    "northeast", "northwest", "southeast", "southwest",
    "port fore", "port aft", "starboard fore", "starboard aft",
    "north", "south", "east", "west",
    "port", "starboard", "fore", "aft",
    "up", "down", "in", "out",
]

_ABBREV: dict[str, str] = {
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}

_NUMBER_PATTERN = r"(?:a |an |the |" + "|".join(_NUMBER_WORDS) + r" )"
_OF_RE = re.compile(
    r"(" + "|".join(_DIRECTIONS) + r")\s+of\s+here\b"
    r"|(?:of|is)\s+(?:" + _NUMBER_PATTERN + r")?\s*(" + "|".join(_DIRECTIONS) + r")\b",
    re.IGNORECASE,
)


def _parse_writtenmap(text: str, ctx: ScriptContext) -> None:
    """Parse Room.Writtenmap prose and update the room graph with discovered exits."""
    exits = {}
    for match in _OF_RE.finditer(text):
        direction = (match.group(1) or match.group(2)).lower()
        direction = _ABBREV.get(direction, direction)
        if direction not in exits:
            exits[direction] = "1"

    if not exits:
        return

    num = ctx._ctx.room.current
    if not num:
        return

    # Log the current room and exits being added for debugging
    room_info = ctx._ctx.room.graph.get_room(num) if ctx._ctx.room.graph else None
    if room_info:
        log.debug("discworld_rooms: updating room %s (%s) with exits %s",
                   num, room_info.name, exits)
    else:
        log.debug("discworld_rooms: updating unknown room %s with exits %s",
                   num, exits)

    ctx.update_room({"identifier": num, "exits": exits})


def _urlretrieve(req: urllib.request.Request, path: str) -> None:
    """Download *req* to *path* with custom headers."""
    with urllib.request.urlopen(req) as resp:
        with open(path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)


async def _import_quowmap(ctx: ScriptContext) -> None:
    """Download and import Quow's map database if not already imported."""
    store = ctx._ctx.room.graph
    row = store.conn.execute(
        "SELECT value FROM meta WHERE key = 'quowmap_imported'"
    ).fetchone()
    if row is not None:
        log.info("discworld_rooms: quowmap already imported, skipping")
        return

    cache_dir = xdg_data_dir()
    cache_path = os.path.join(str(cache_dir), "discworld-quowmap.db")

    try:
        log.info("discworld_rooms: downloading quowmap database from %s", _QUOWMAP_URL)
        req = urllib.request.Request(
            _QUOWMAP_URL,
            headers={"User-Agent": "telix/0.1"},
        )
        await asyncio.to_thread(_urlretrieve, req, cache_path)
        log.info("discworld_rooms: download complete, importing...")
        count = store.import_quowmap(cache_path)
        ctx.print(f"discworld_rooms: imported {count} rooms from Quow's map database")
    except Exception as exc:
        log.warning("discworld_rooms: failed to import quowmap database: %s", exc)
        ctx.print(f"discworld_rooms: could not import map database ({exc})")


async def run(ctx: ScriptContext) -> None:
    """Register a Room.Writtenmap callback and keep the script alive."""
    await _import_quowmap(ctx)
    ctx.on_gmcp("Room.Writtenmap", lambda data: _parse_writtenmap(data, ctx))
    ctx.print("discworld_rooms: exit parser registered")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
