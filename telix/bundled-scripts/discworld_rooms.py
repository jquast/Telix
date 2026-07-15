"""Parse Discworld Room.Writtenmap to discover exits from the current room.

Discworld sends exit information as prose in ``Room.Writtenmap`` rather
than as structured data in ``Room.Info``.  The prose describes nearby
rooms using an ``of N D`` pattern, e.g.::

    Exits south and north of one west and the limit of your vision is
    one west from here.
    doors east and northeast of one north, an exit southwest of one
    north, exits south, southeast and west of two south.

When a room at ``N D`` is described, the current room has an exit in
direction ``D``.  This script extracts those directions and feeds them
into the room graph so that ``randomwalk`` and ``autodiscover`` work.

Start it as a background daemon at the beginning of a session::

    `async discworld_rooms`
"""

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


async def run(ctx: ScriptContext) -> None:
    ctx.print("[discworld_rooms] started, monitoring Room.Writtenmap")

    while True:
        if not await ctx.gmcp_changed("Room.Writtenmap", timeout=None):
            break

        text = ctx.gmcp_get("Room.Writtenmap")
        if not text:
            continue

        exits = {}
        for match in _OF_RE.finditer(text):
            direction = match.group(1).lower()
            if direction not in exits:
                exits[direction] = "1"

        if exits and ctx.room is not None:
            ctx.update_room({"identifier": ctx.room.num, "exits": exits})
