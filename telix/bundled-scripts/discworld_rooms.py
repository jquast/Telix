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

Start it as a background daemon at the beginning of a session::

    `async discworld_rooms`
"""

import asyncio
import re
from telix.scripts import ScriptContext

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

_NUMBER_PATTERN = r"(?:a |an |the |" + "|".join(_NUMBER_WORDS) + r" )"
_OF_RE = re.compile(
    r"(?:of|is)\s+(?:" + _NUMBER_PATTERN + r")?\s*(" + "|".join(_DIRECTIONS) + r")\b",
    re.IGNORECASE,
)


def _parse_writtenmap(text: str, ctx: ScriptContext) -> None:
    """Parse Room.Writtenmap prose and update the room graph with discovered exits."""
    exits = {}
    for match in _OF_RE.finditer(text):
        direction = match.group(1).lower()
        if direction not in exits:
            exits[direction] = "1"

    if not exits:
        return

    num = ctx._ctx.room.current
    if not num:
        return

    ctx.update_room({"identifier": num, "exits": exits})


async def run(ctx: ScriptContext) -> None:
    """Register a Room.Writtenmap callback and keep the script alive."""
    ctx.on_gmcp("Room.Writtenmap", lambda data: _parse_writtenmap(data, ctx))
    ctx.print("discworld_rooms: exit parser registered")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
