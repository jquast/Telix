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
import collections
import urllib.request

from telix.paths import xdg_data_dir
from telix.scripts import ScriptContext

log = logging.getLogger(__name__)

_QUOWMAP_URL = "https://quow.co.uk/maps/_quowmap_database.db"

_NUMBER_WORDS = {
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
}

_DIRECTIONS = [
    "northeast",
    "northwest",
    "southeast",
    "southwest",
    "port fore",
    "port aft",
    "starboard fore",
    "starboard aft",
    "north",
    "south",
    "east",
    "west",
    "port",
    "starboard",
    "fore",
    "aft",
    "up",
    "down",
    "in",
    "out",
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


_BRACKET_RE = re.compile(r"\[([^\]]+)\]")


def _parse_bracket_exits(text: str) -> dict[str, str]:
    """
    Parse compact Writtenmap format like 'A tomato farm [e,s,w].'.

    Returns a dict of {direction: "1"} for each exit found in the bracket-enclosed, comma-separated list of abbreviated
    directions.
    """
    exits: dict[str, str] = {}
    m = _BRACKET_RE.search(text)
    if not m:
        return exits
    for token in m.group(1).split(","):
        token = token.strip()
        if not token:
            continue
        # Accept abbreviated directions (n, s, e, w, ne, nw, se, sw, etc.)
        # as-is; they're already in short form.
        exits[token] = "1"
    return exits


def _parse_writtenmap(text: str, ctx: ScriptContext) -> None:
    """Parse Room.Writtenmap prose and update the room graph with discovered exits."""
    exits: dict[str, str] = {}

    # Format 1: compact bracket style "[e,s,w]"
    exits.update(_parse_bracket_exits(text))

    # Format 2: prose "of N D" style
    for match in _OF_RE.finditer(text):
        direction = (match.group(1) or match.group(2)).lower()
        direction = _ABBREV.get(direction, direction)
        if direction not in exits:
            exits[direction] = "1"

    # Catch compound "X and Y of here" patterns where the main regex
    # only catches the second direction (the one adjacent to "of here").
    # E.g. "Doors north and south of here" → main regex catches "south"
    # from "south of here" but misses "north" because "north and south"
    # separates it from "of here".
    _COMPOUND_RE = re.compile(
        r"\b(" + "|".join(_DIRECTIONS) + r")\s+and\s+(" + "|".join(_DIRECTIONS) + r")\s+of\s+here\b", re.IGNORECASE
    )
    for m in _COMPOUND_RE.finditer(text):
        d = _ABBREV.get(m.group(1).lower(), m.group(1).lower())
        if d not in exits:
            exits[d] = "1"

    if not exits:
        return

    num = ctx._ctx.room.current
    if not num:
        return

    # Log the current room and exits being added for debugging
    room_info = ctx._ctx.room.graph.get_room(num) if ctx._ctx.room.graph else None
    if room_info:
        log.debug("discworld_rooms: updating room %s (%s) with exits %s", num, room_info.name, exits)
    else:
        log.debug("discworld_rooms: updating unknown room %s with exits %s", num, exits)

    ctx.update_room({"identifier": num, "exits": exits})


# Room description text pattern: "There are 3 obvious exits: east, south and west."
_RE_EXIT_TEXT = re.compile(r"There are \w+ obvious exits?: (.+?)(?:\.\s*)?$", re.IGNORECASE | re.MULTILINE)
# Direction words in exit text: "east", "journey north", "southwest"
_RE_TEXT_DIR = re.compile(
    r"(?:journey\s+)?(north|south|east|west"
    r"|northeast|northwest|southeast|southwest"
    r"|up|down|in|out)",
    re.IGNORECASE,
)


def _exits_from_text(text: str) -> dict[str, str]:
    """
    Parse room description exit text into {direction: '1'}.

    Handles lines like 'There are three obvious exits: east, south and west.'
    """
    m = _RE_EXIT_TEXT.search(text)
    if not m:
        return {}
    exits: dict[str, str] = {}
    for dm in _RE_TEXT_DIR.finditer(m.group(1)):
        d = dm.group(1).lower()
        short = _ABBREV.get(d, d)
        exits[short] = "1"
    return exits


async def _watch_exits_task(ctx: ScriptContext) -> None:
    """
    Background task: watch room description text for exit patterns and populate the room graph.

    Some Discworld areas (e.g. Ephebe farmland) send exits only in room description text rather than in GMCP
    Room.Writtenmap prose. This catches 'There are N obvious exits: dir1, dir2 and dir3.' patterns in any server output.
    """
    while True:
        match = await ctx.wait_for(r"There are \w+ obvious exits?: (.+?)(?:\.\s*)?$", timeout=999999)
        if match is None:
            continue
        exits_text = match.group(1)
        exits: dict[str, str] = {}
        for dm in _RE_TEXT_DIR.finditer(exits_text):
            d = dm.group(1).lower()
            short = _ABBREV.get(d, d)
            exits[short] = "1"
        if exits:
            num = ctx._ctx.room.current
            if num:
                log.debug("discworld_rooms: text-exits for %s: %s", num[:12], exits)
                ctx.update_room({"identifier": num, "exits": exits})


# Recently seen rooms for sign/plaque reading — don't re-read signs in
# rooms visited within the last ~100 moves.
_seen_rooms: set[str] = set()
_seen_order: collections.deque[str] = collections.deque(maxlen=100)


def _mark_room_seen(room_id: str) -> bool:
    """
    Mark a room as seen.

    Returns True if the room was already seen.
    """
    if room_id in _seen_rooms:
        return True
    if len(_seen_order) >= 100:
        old = _seen_order.popleft()
        _seen_rooms.discard(old)
    _seen_rooms.add(room_id)
    _seen_order.append(room_id)
    return False


async def _read_signs_task(ctx: ScriptContext) -> None:
    """
    Background task: read signs/plaques when found in room description text.

    Only fires once per room change — rooms get re-read only after at least 100 other rooms have been visited.
    """
    while True:
        # Match a line containing 'sign' or 'plaque' that is NOT a command
        # echo (> read sign) or a read-response (You read the sign:).
        match = await ctx.wait_for(
            r"(?i)(?:\n|^)(?![>] |You read the )"
            r"[^\n]*\b(sign|plaque)s?\b",
            timeout=999999,
        )
        if match is None:
            continue

        num = ctx._ctx.room.current
        if not num:
            continue
        if _mark_room_seen(num):
            continue

        word = match.group(1).rstrip("s")
        ctx.print(f"discworld_rooms: reading {word} in room {num[:12]}")
        await ctx.send(f"read {word}")


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
    row = store.conn.execute("SELECT value FROM meta WHERE key = 'quowmap_imported'").fetchone()
    if row is not None:
        log.info("discworld_rooms: quowmap already imported, skipping")
        return

    cache_dir = xdg_data_dir()
    cache_path = os.path.join(str(cache_dir), "discworld-quowmap.db")

    try:
        log.info("discworld_rooms: downloading quowmap database from %s", _QUOWMAP_URL)
        req = urllib.request.Request(_QUOWMAP_URL, headers={"User-Agent": "telix/0.1"})
        await asyncio.to_thread(_urlretrieve, req, cache_path)
        log.info("discworld_rooms: download complete, importing...")
        count = store.import_quowmap(cache_path)
        ctx.print(f"discworld_rooms: imported {count} rooms from Quow's map database")
    except Exception as exc:
        log.warning("discworld_rooms: failed to import quowmap database: %s", exc)
        ctx.print(f"discworld_rooms: could not import map database ({exc})")


# Map quowmap area IDs to human-readable names.
# Extend this dict as needed — the Room Browser will show the mapped name.
_AREA_NAMES: dict[str, str] = {
    "1": "Ankh-Morpork",
    "2": "AM Assassins",
    "3": "AM Buildings",
    "4": "AM Cruets",
    "5": "AM Docks",
    "6": "AM Guilds",
    "7": "AM Isle of Gods",
    "8": "AM Shades",
    "9": "AM Temple of Small Gods",
    "10": "AM Temples",
    "11": "AM Thieves",
    "12": "Unseen University",
    "13": "AM Warriors",
    "14": "AM Watch House",
    "15": "Magpyr's Castle",
    "16": "Bois",
    "17": "Bes Pelargic",
    "18": "BP Buildings",
    "19": "BP Estates",
    "20": "BP Wizards",
    "21": "Brown Islands",
    "22": "Death's Domain",
    "23": "Djelibeybi",
    "24": "DJB Wizards",
    "25": "Ephebe",
    "26": "Ephebe Underdocks",
    "27": "Genua",
    "28": "Genua Sewers",
    "29": "GRFLX Caves",
    "30": "Hashishim Caves",
    "31": "Klatch",
    "32": "Lancre",
    "33": "Mano Rossa",
    "34": "Monks of Cool",
    "35": "Netherworld",
    "37": "Pumpkin Town",
    "38": "Ramtops",
    "39": "Sto-Lat",
    "40": "Academy of Artificers",
    "41": "Cabbage Warehouse",
    "43": "Sto-Lat Sewers",
    "44": "Sprite Caves",
    "45": "Sto Plains",
    "46": "Uberwald",
    "48": "Klatchian Farmsteads",
    "49": "CTF Arena",
    "50": "PK Arena",
    "51": "AM Post Office",
    "52": "BP Ninjas",
    "53": "The Travelling Shop",
    "54": "Slippery Hollow",
    "55": "Creel House of Magic",
    "56": "Special Areas",
    "57": "Skund Wolf Trail",
    "59": "Copperhead",
    "60": "Ephebe Citadel",
    "61": "AM Fool's Guild",
    "62": "Thursday's Island",
    "63": "SS Unsinkable",
    "64": "Passage Rooms",
    "65": "Sto Plains Hedge Wizards",
    "99": "Discworld Overworld",
}


async def run(ctx: ScriptContext) -> None:
    """Register a Room.Writtenmap callback, start the text-based exit watcher and sign reader, register area name
    mappings, and keep the script alive."""
    await _import_quowmap(ctx)
    ctx.area_names.update(_AREA_NAMES)
    ctx.on_gmcp("Room.Writtenmap", lambda data: _parse_writtenmap(data, ctx))
    asyncio.create_task(_watch_exits_task(ctx))
    asyncio.create_task(_read_signs_task(ctx))
    ctx.print("discworld_rooms: exit parser, sign reader, and area names registered")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
