"""
Room graph tracking, BFS pathfinding, and SQLite persistence for GMCP Room.Info data.

Incrementally builds a directed graph from GMCP ``Room.Info`` messages, supports shortest-path search via BFS, and
persists per-session room data to ``~/.local/share/telix/rooms-{host}_{port}.db``.
"""

import os
import re
import json
import random
import typing
import hashlib
import logging
import sqlite3
import datetime
import collections
import dataclasses

from . import paths

EXIT_DIR_RE = re.compile(
    r"\s*"
    r"(?:\{[^}]*\}\s*)?"
    r"\[(?:[nswe]|n[ew]|s[ew]|[a-z]+)"
    r"(?:,(?:[nswe]|n[ew]|s[ew]|[a-z]+))*\]"
    r"\s*$"
)

MOVEMENT_DIRS = frozenset(
    {
        "n",
        "s",
        "e",
        "w",
        "ne",
        "nw",
        "se",
        "sw",
        "north",
        "south",
        "east",
        "west",
        "northeast",
        "northwest",
        "southeast",
        "southwest",
        "port",
        "starboard",
        "fore",
        "aft",
        "port fore",
        "port aft",
        "starboard fore",
        "starboard aft",
        "up",
        "down",
        "in",
        "out",
    }
)


ROOM_ID_KEYS = ("num", "vnum", "id", "identifier")

log = logging.getLogger(__name__)


def room_id(info: dict[str, typing.Any]) -> str | None:
    """
    Extract the room identifier from a GMCP ``Room.Info`` payload.

    Checks ``num``, ``vnum``, and ``id`` in priority order.  When none of these keys are present, a 12-character SHA-1
    hash is generated from the room name and full exit data (direction + destination name pairs) to produce a stable
    synthetic identifier.

    :param info: GMCP Room.Info dict.
    :returns: Room identifier as a string, or None if no key is found.
    """
    for key in ROOM_ID_KEYS:
        if key in info:
            return str(info[key])
    name = info.get("name")
    if name is None:
        return None
    exits = info.get("exits")
    if isinstance(exits, dict) and exits:
        pairs = ",".join(f"{k}:{exits[k]}" for k in sorted(exits))
        fingerprint = f"{name}|{pairs}"
    else:
        fingerprint = str(name)
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]


def strip_exit_dirs(name: str) -> str:
    """
    Strip trailing exit-direction lists like ``[n,s,w,e]`` from a room name.

    Also handles optional ``{SPICE}``-style tags before the bracket list.
    """
    return EXIT_DIR_RE.sub("", name)


@dataclasses.dataclass
class Room:
    """
    A single room in the GMCP room graph.

    Accessible in scripts as ``ctx.room`` (current room) or via :meth:`~telix.rooms.RoomStore.get_room`.
    """

    num: str
    name: str = ""
    area: str = ""
    environment: str = ""
    exits: dict[str, str] = dataclasses.field(default_factory=dict)
    bookmarked: bool = False
    visit_count: int = 0
    last_visited: str = ""
    x: int = 0
    y: int = 0
    blocked: bool = False
    home: bool = False
    marked: bool = False


class RoomStore:
    """
    SQLite-backed room graph with in-memory adjacency cache.

    Accessible in scripts as ``ctx.room_graph``.
    """

    def __init__(self, db_path: str, read_only: bool = False, session_key: str = "") -> None:
        """
        Open or create an SQLite room database.

        :param db_path: Path to the .db file.
        :param read_only: Open in read-only mode (no table creation).
        :param session_key: host:port identifier stored as metadata.
        """
        dir_path = os.path.dirname(db_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        mode = "ro" if read_only else "rwc"
        uri = f"file:{db_path}?mode={mode}"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA cache_size=-4000")  # 4 MB
        self.conn.execute("PRAGMA mmap_size=8388608")  # 8 MB
        if not read_only:
            self.create_tables()
            if session_key:
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('session_key', ?)", (session_key,))
                self.conn.commit()
        self.adj: dict[str, dict[str, str]] = {}
        self.area_names: dict[str, str] = {}
        self.load_adjacency()

    def create_tables(self) -> None:
        """Create schema tables if they do not exist."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS room ( num TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', area TEXT NOT NULL
            DEFAULT '', environment TEXT NOT NULL DEFAULT '', bookmarked INTEGER NOT NULL DEFAULT 0, visit_count INTEGER
            NOT NULL DEFAULT 0,

            last_visited TEXT NOT NULL DEFAULT '', x INTEGER NOT NULL DEFAULT 0, y INTEGER NOT NULL DEFAULT 0 ); CREATE
            TABLE IF NOT EXISTS exit ( src_num TEXT NOT NULL, direction TEXT NOT NULL,     dst_num TEXT NOT NULL,
            PRIMARY KEY (src_num, direction) ); CREATE TABLE IF NOT EXISTS meta (     key TEXT PRIMARY KEY,     value
            TEXT NOT NULL );
            """
        )
        for col in ("blocked", "home", "marked"):
            try:
                self.conn.execute(f"ALTER TABLE room ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
        for col in ("contents",):
            try:
                self.conn.execute(f"ALTER TABLE room ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        for col in ("x", "y"):
            try:
                self.conn.execute(f"ALTER TABLE room ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
        self.conn.execute("INSERT OR IGNORE INTO meta VALUES ('version', '1')")
        self.conn.commit()

    def load_adjacency(self) -> None:
        """Load full adjacency graph into memory for BFS."""
        self.adj.clear()
        try:
            for src, direction, dst in self.conn.execute(
                "SELECT src_num, direction, dst_num FROM exit"
                " WHERE src_num IN (SELECT num FROM room)"
                " AND dst_num IN (SELECT num FROM room)"
            ):
                self.adj.setdefault(src, {})[direction] = dst
        except sqlite3.OperationalError:
            pass

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()

    def row_to_room(self, row: tuple[typing.Any, ...]) -> Room:
        """Convert a SELECT row to a :class:`Room`."""
        num = row[0]
        exits = dict(self.adj.get(num, {}))
        return Room(
            num=num,
            name=row[1],
            area=row[2],
            environment=row[3],
            exits=exits,
            bookmarked=bool(row[4]),
            visit_count=row[5],
            last_visited=row[6],
            x=row[7],
            y=row[8],
            blocked=bool(row[9]),
            home=bool(row[10]),
            marked=bool(row[11]),
        )

    ROOM_COLS = "num, name, area, environment, bookmarked, visit_count, last_visited, x, y, blocked, home, marked"

    @property
    def rooms(self) -> dict[str, Room]:
        """
        Return all rooms as a dict (for compatibility).

        Not cached.
        """
        result: dict[str, Room] = {}
        for row in self.conn.execute(f"SELECT {self.ROOM_COLS} FROM room"):
            room = self.row_to_room(row)
            result[room.num] = room
        return result

    def room_summaries(self) -> list[tuple[str, str, str, int, bool, str, bool, bool, bool]]:
        """
        Return lightweight room summary tuples.

        Each tuple is ``(num, name, area, exit_count, bookmarked,
        last_visited, blocked, home, marked)``.

        Counts exits via SQL aggregation instead of materialising
        :class:`Room` objects, which avoids copying exit dicts for every
        room.
        """
        rows = self.conn.execute(
            "SELECT r.num, r.name, r.area, COUNT(e.direction),"
            " r.bookmarked, r.last_visited, r.blocked, r.home, r.marked"
            " FROM room r LEFT JOIN exit e ON r.num = e.src_num"
            " GROUP BY r.num"
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], bool(r[4]), r[5], bool(r[6]), bool(r[7]), bool(r[8])) for r in rows]

    def room_area(self, num: str) -> str:
        """Return the area of a single room, or ``""`` if not found."""
        row = self.conn.execute("SELECT area FROM room WHERE num = ?", (num,)).fetchone()
        return row[0] if row else ""

    def get_room(self, num: str) -> Room | None:
        """
        Get a single room by number.

        :param num: Room number.
        :returns: :class:`Room` or None if not found.
        """
        row = self.conn.execute(f"SELECT {self.ROOM_COLS} FROM room WHERE num = ?", (num,)).fetchone()
        if row is None:
            return None
        return self.row_to_room(row)

    def has_room(self, num: str) -> bool:
        """Return ``True`` if room *num* exists in the database."""
        row = self.conn.execute("SELECT 1 FROM room WHERE num = ?", (num,)).fetchone()
        return row is not None

    def update_room(self, info: dict[str, typing.Any]) -> None:
        """
        Update or create a room from a GMCP ``Room.Info`` payload.

        When optional fields (``name``, ``area``, ``environment``) are absent from *info*, the existing database values
        are preserved.

        :param info: GMCP Room.Info dict with a room identifier key.
        """
        num = room_id(info) or ""
        if not num:
            return

        has_exits = "exits" in info
        exits: dict[str, str]
        if has_exits:
            raw = info["exits"]
            if isinstance(raw, dict):
                exits = {str(k): str(v) for k, v in raw.items() if v}
            else:
                exits = {}
        else:
            exits = self.adj.get(num, {})

        name = strip_exit_dirs(str(info.get("name", "")))
        area = str(info.get("area", ""))
        environment = str(info.get("environment", ""))
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Preserve existing values for optional fields not present in this
        # update -- prevents partial updates (e.g. from script exit-parsers
        # that only pass {"identifier": ..., "exits": ...}) from zeroing out
        # name/area/environment.
        if "name" not in info or "area" not in info or "environment" not in info:
            existing = self.get_room(num)
            if existing is not None:
                if "name" not in info:
                    name = existing.name
                if "area" not in info:
                    area = existing.area
                if "environment" not in info:
                    environment = existing.environment

        self.conn.execute(
            "INSERT INTO room"
            " (num, name, area, environment, visit_count, last_visited)"
            " VALUES (?, ?, ?, ?, 1, ?)"
            " ON CONFLICT(num) DO UPDATE SET"
            " name=excluded.name, area=excluded.area,"
            " environment=excluded.environment,"
            " visit_count=visit_count+1,"
            " last_visited=excluded.last_visited",
            (num, name, area, environment, now),
        )
        if has_exits:
            # If the incoming exits contain placeholder values (e.g. "1"
            # from Writtenmap parsers that don't know destination IDs),
            # check whether this room already has corrected (non-placeholder)
            # destinations for those directions and preserve them.
            if any(v in ("", "1") for v in exits.values()):
                # First, preserve non-placeholder destinations from the DB
                # (persisted from previous sessions).
                existing_exits = dict(
                    self.conn.execute("SELECT direction, dst_num FROM exit WHERE src_num = ?", (num,)).fetchall()
                )
                # Also preserve non-placeholder destinations from the
                # in-memory adj cache (corrected by movement in the
                # current session, not yet in the DB).
                adj_exits = self.adj.get(num, {})
                for direction, dst in exits.items():
                    if dst in ("", "1"):
                        preserved = None
                        if direction in existing_exits:
                            preserved = existing_exits[direction]
                        if (not preserved or preserved in ("", "1")) and direction in adj_exits:
                            preserved = adj_exits[direction]
                        if preserved and preserved not in ("", "1"):
                            exits[direction] = preserved
                            log.debug("update_room: preserved %s exit of %s -> %s", direction, num[:8], preserved[:8])

            # Upsert only the Writtenmap-provided exits so that any
            # previously-recorded (movement-corrected) exits for directions
            # not mentioned in this Writtenmap are preserved.
            for direction, dst in exits.items():
                self.conn.execute(
                    "INSERT INTO exit (src_num, direction, dst_num)"
                    " VALUES (?, ?, ?)"
                    " ON CONFLICT(src_num, direction)"
                    " DO UPDATE SET dst_num=excluded.dst_num",
                    (num, direction, dst),
                )
        self.conn.commit()
        # Merge Writtenmap exits into the in-memory adj cache rather than
        # replacing it -- preserves movement-corrected edges for directions
        # that the Writtenmap doesn't mention this time.
        self.adj[num] = {**self.adj.get(num, {}), **exits}

    def import_quowmap(self, quowmap_path: str) -> int:
        """
        Import rooms and exits from Quow's Discworld map database.

        Reads the quowmap SQLite database (room_id, room_short, map_id, room_type, xpos, ypos + room_exits table) and
        bulk-inserts into the local RoomStore.  Sets the meta key ``quowmap_imported`` to ``1`` on success.

        :param quowmap_path: Path to the quowmap database file.
        :returns: Number of rooms imported.
        """
        qconn = sqlite3.connect(quowmap_path)
        try:
            rooms = qconn.execute("SELECT room_id, room_short, map_id, room_type, xpos, ypos FROM rooms").fetchall()
            exits = qconn.execute("SELECT room_id, exit, connect_id FROM room_exits").fetchall()
        finally:
            qconn.close()

        if not rooms:
            return 0

        self.conn.execute("BEGIN TRANSACTION")
        self.conn.executemany(
            "INSERT OR REPLACE INTO room"
            " (num, name, area, environment, x, y, visit_count, last_visited)"
            " VALUES (?, ?, ?, ?, ?, ?, 0, '')",
            [(r[0], r[1], str(r[2]), r[3], r[4], r[5]) for r in rooms],
        )
        valid_exits = [e for e in exits if e[1] in MOVEMENT_DIRS]
        skipped = len(exits) - len(valid_exits)
        if skipped:
            log.info(
                "import_quowmap: skipped %d non-movement exits (e.g. %s)",
                skipped,
                ", ".join(repr(e[1]) for e in exits if e[1] not in MOVEMENT_DIRS)[:120],
            )
        self.conn.executemany("INSERT OR REPLACE INTO exit (src_num, direction, dst_num) VALUES (?, ?, ?)", valid_exits)
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('quowmap_imported', '1')")
        self.conn.commit()
        self.load_adjacency()
        log.info("imported %d rooms and %d exits from quowmap", len(rooms), len(exits))
        return len(rooms)

    def cleanup_bogus_edges(self) -> int:
        """
        Remove exits whose direction is not a recognised movement command.

        Non-movement exit directions (e.g. ``look in corpse``) can leak into a quowmap import or be recorded when
        ``active_command`` is stale.  After calling this the in-memory adjacency cache is reloaded.

        :returns: Number of rows deleted.
        """
        placeholders = ",".join("?" * len(MOVEMENT_DIRS))
        cur = self.conn.execute(f"DELETE FROM exit WHERE direction NOT IN ({placeholders})", tuple(MOVEMENT_DIRS))
        self.conn.commit()
        count = cur.rowcount
        if count:
            log.info("cleanup: removed %d bogus edges", count)
        self.load_adjacency()
        return count

    def save_room_contents(self, num: str, text: str) -> None:
        """
        Save or replace room contents text, trimming to 4096 characters.

        :param num: Room number.
        :param text: Plain text content (ANSI sequences already stripped).
        """
        text = text[:4096]
        self.conn.execute(
            "INSERT INTO room (num, contents) VALUES (?, ?) ON CONFLICT(num) DO UPDATE SET contents=excluded.contents",
            (num, text),
        )
        self.conn.commit()

    def get_room_contents(self, num: str) -> str:
        """
        Return the stored contents text for a room.

        :param num: Room number.
        :returns: Contents string, or ``""`` if not found.
        """
        row = self.conn.execute("SELECT contents FROM room WHERE num = ?", (num,)).fetchone()
        return row[0] if row and row[0] else ""

    def search_rooms_by_contents(self, query: str) -> list[str]:
        """
        Return room IDs whose contents match *query*.

        :param query: Case-insensitive substring.
        :returns: List of matching room number strings.
        """
        q = f"%{query}%"
        rows = self.conn.execute("SELECT num FROM room WHERE contents LIKE ? COLLATE NOCASE", (q,)).fetchall()
        return [r[0] for r in rows]

    MARKER_COLS = ("bookmarked", "blocked", "home", "marked")

    def set_marker(self, num: str, marker: str) -> bool:
        """
        Toggle a marker on a room, clearing all other markers.

        Markers are mutually exclusive: only one of ``bookmarked``,
        ``blocked``, ``home``, ``marked`` can be set at a time.  If the
        requested marker is already set, all markers are cleared.

        For ``home``, the one-per-area constraint is also enforced.

        :param num: Room number.
        :param marker: One of "bookmarked", "blocked", "home",
            ``"marked"``.
        :returns: New state of *marker*, or False if room not found.
        """
        if marker not in self.MARKER_COLS:
            raise ValueError(f"unknown marker: {marker!r}")
        row = self.conn.execute(f"SELECT {marker}, area FROM room WHERE num = ?", (num,)).fetchone()
        if row is None:
            return False
        new_state = not bool(row[0])
        self.conn.execute("UPDATE room SET bookmarked=0, blocked=0, home=0, marked=0 WHERE num = ?", (num,))
        if new_state:
            self.conn.execute(f"UPDATE room SET {marker} = 1 WHERE num = ?", (num,))
            if marker == "home":
                area = row[1]
                self.conn.execute("UPDATE room SET home = 0 WHERE area = ? AND num != ?", (area, num))
        self.conn.commit()
        return new_state

    def toggle_bookmark(self, num: str) -> bool:
        """Toggle bookmark on a room (exclusive with other markers)."""
        return self.set_marker(num, "bookmarked")

    def toggle_blocked(self, num: str) -> bool:
        """Toggle blocked state on a room (exclusive with other markers)."""
        return self.set_marker(num, "blocked")

    def toggle_home(self, num: str) -> bool:
        """Toggle home state on a room (exclusive with other markers)."""
        return self.set_marker(num, "home")

    def toggle_marked(self, num: str) -> bool:
        """Toggle mark on a room (exclusive with other markers)."""
        return self.set_marker(num, "marked")

    def get_home_for_area(self, area: str) -> str | None:
        """
        Return the home room number for the given area.

        :param area: Area name.
        :returns: Room number string, or None if no home is set.
        """
        row = self.conn.execute("SELECT num FROM room WHERE area = ? AND home = 1", (area,)).fetchone()
        return row[0] if row else None

    def blocked_rooms(self) -> frozenset[str]:
        """Return frozenset of all blocked room numbers."""
        return frozenset(row[0] for row in self.conn.execute("SELECT num FROM room WHERE blocked = 1"))

    def room_nums(self) -> frozenset[str]:
        """Return the set of all room numbers in the database."""
        return frozenset(row[0] for row in self.conn.execute("SELECT num FROM room"))

    def bfs_distances(self, src: str, blocked: frozenset[str] = frozenset()) -> dict[str, int]:
        """
        BFS from *src* returning distance to every reachable room.

        :param src: Source room number.
        :param blocked: Room numbers to treat as impassable.
        :returns: dict {room_num: distance} for all reachable rooms.
        """
        known = self.room_nums()
        if src not in known:
            return {}
        distances: dict[str, int] = {src: 0}
        queue: collections.deque[str] = collections.deque([src])
        while queue:
            current = queue.popleft()
            d = distances[current]
            for target in self.adj.get(current, {}).values():
                if target not in distances and target in known and target not in blocked:
                    distances[target] = d + 1
                    queue.append(target)
        return distances

    def find_path(self, src: str, dst: str, blocked: frozenset[str] = frozenset()) -> list[str] | None:
        """
        BFS shortest path from *src* to *dst*.

        :param blocked: Room numbers to treat as impassable.
        :returns: List of direction names, or None if unreachable.
        """
        if src == dst:
            return []
        if not self.has_room(src):
            return None

        visited: set[str] = {src}
        queue: collections.deque[tuple[str, list[str]]] = collections.deque([(src, [])])

        while queue:
            current, path = queue.popleft()
            for direction, target in self.adj.get(current, {}).items():
                if target == dst:
                    return path + [direction]
                if target not in visited and self.has_room(target) and target not in blocked:
                    visited.add(target)
                    queue.append((target, path + [direction]))

        return None

    def find_path_with_rooms(
        self, src: str, dst: str, blocked: frozenset[str] = frozenset()
    ) -> list[tuple[str, str]] | None:
        """
        BFS shortest path returning ``[(direction, target_room_num), ...]``.

        :param blocked: Room numbers to treat as impassable.
        :returns: List of (direction, expected_room_num) pairs, or None.
        """
        if src == dst:
            return []
        if not self.has_room(src):
            return None

        visited: set[str] = {src}
        queue: collections.deque[tuple[str, list[tuple[str, str]]]] = collections.deque([(src, [])])

        while queue:
            current, path = queue.popleft()
            for direction, target in self.adj.get(current, {}).items():
                if target == dst:
                    return path + [(direction, target)]
                if target not in visited and self.has_room(target) and target not in blocked:
                    visited.add(target)
                    queue.append((target, path + [(direction, target)]))

        return None

    def find_same_name(self, num: str, limit: int = 99) -> list[Room]:
        """
        Find rooms with the same name as *num*, sorted by least-recently-visited.

        :param num: Room number to match name against.
        :param limit: Maximum results to return.
        :returns: List of matching rooms, excluding *num* itself.
        """
        row = self.conn.execute("SELECT name FROM room WHERE num = ?", (num,)).fetchone()
        if row is None or not row[0]:
            return []
        target_name = row[0]
        rows = self.conn.execute(
            f"SELECT {self.ROOM_COLS} FROM room WHERE name = ? AND num != ? ORDER BY last_visited ASC LIMIT ?",
            (target_name, num, limit),
        ).fetchall()
        return [self.row_to_room(r) for r in rows]

    def find_branches(
        self, src: str, limit: int = 99, blocked: frozenset[str] = frozenset(), strategy: str = "bfs"
    ) -> list[tuple[str, str, str]]:
        """
        Find exits from known rooms leading to unvisited or unknown rooms.

        :param src: Source room number to search from.
        :param limit: Maximum number of branches to return.
        :param blocked: Room numbers to treat as impassable.
        :param strategy: search order "bfs" for nearest-first, "dfs" for deepest-first ordering.
        :returns: list [(gateway_room_num, direction, target_num), ...] sorted by BFS distance from *src*.
        """
        if not self.has_room(src):
            return []

        adj_src = self.adj.get(src)
        if not adj_src:
            log.debug("find_branches: src %s has no exits in adj cache", src[:12])

        visited: set[str] = {src}
        queue: collections.deque[tuple[str, int]] = collections.deque([(src, 0)])
        branches: list[tuple[int, str, str, str]] = []

        while queue:
            current, dist = queue.popleft()
            for direction, target in self.adj.get(current, {}).items():
                if target in blocked:
                    continue
                target_vc = self.conn.execute("SELECT visit_count FROM room WHERE num = ?", (target,)).fetchone()
                if target_vc is None or target_vc[0] == 0:
                    branches.append((dist, current, direction, target))
                elif target not in visited:
                    visited.add(target)
                    queue.append((target, dist + 1))

        reverse = strategy == "dfs"
        branches.sort(key=lambda b: b[0], reverse=reverse)
        shuffled: list[tuple[int, str, str, str]] = []
        i = 0
        while i < len(branches):
            dist = branches[i][0]
            j = i
            while j < len(branches) and branches[j][0] == dist:
                j += 1
            tier = branches[i:j]
            random.shuffle(tier)
            shuffled.extend(tier)
            i = j
        return [(gw, d, t) for _, gw, d, t in shuffled[:limit]]

    def search(self, query: str) -> list[Room]:
        """
        Case-insensitive substring search on room name, area, ID, and contents.

        :param query: Search string.
        :returns: Matching rooms sorted bookmarked-first, then by name.
        """
        q = f"%{query}%"
        rows = self.conn.execute(
            f"SELECT {self.ROOM_COLS}"
            " FROM room WHERE name LIKE ? COLLATE NOCASE"
            " OR area LIKE ? COLLATE NOCASE"
            " OR num LIKE ? COLLATE NOCASE"
            " OR contents LIKE ? COLLATE NOCASE",
            (q, q, q, q),
        ).fetchall()
        results = [self.row_to_room(r) for r in rows]
        results.sort(key=lambda r: (not r.bookmarked, r.name.lower()))
        return results


RoomGraph = RoomStore


def xdg_data_dir() -> str:
    """Return XDG data directory for telix."""
    return paths.DATA_DIR


def session_file_path(prefix: str, session_key: str, ext: str = "") -> str:
    """Return a per-session file path under the XDG data directory."""
    return os.path.join(xdg_data_dir(), f"{prefix}{paths.safe_session_slug(session_key)}{ext}")


def rooms_path(session_key: str) -> str:
    """Return path to room graph SQLite DB for *session_key* (``host:port``)."""
    return session_file_path("rooms-", session_key, ".db")


def current_room_path(session_key: str) -> str:
    """Return path to current room number file for *session_key*."""
    return session_file_path(".current-room-", session_key)


def fasttravel_path(session_key: str) -> str:
    """Return path to fast travel command file for *session_key*."""
    return session_file_path(".fasttravel-", session_key)


def prefs_path(session_key: str) -> str:
    """Return path to preferences JSON for *session_key* (``host:port``)."""
    return session_file_path("prefs-", session_key, ".json")


def load_prefs(session_key: str) -> dict[str, bool | str]:
    """
    Load per-session preferences from disk.

    :param session_key: Session identifier (host:port).
    :returns: Dict of preference values (booleans and strings).
    """
    path = prefs_path(session_key)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            result: dict[str, bool | str] = {}
            for k, v in data.items():
                if isinstance(v, str):
                    result[str(k)] = v
                else:
                    result[str(k)] = bool(v)
            return result
    except (OSError, ValueError):
        pass
    return {}


def save_prefs(session_key: str, prefs: dict[str, bool | str]) -> None:
    """
    Atomically save per-session preferences to disk.

    :param session_key: Session identifier (host:port).
    :param prefs: Dict of preference values (booleans and strings).
    """
    path = prefs_path(session_key)
    paths.atomic_write(path, json.dumps(prefs, separators=(",", ":")))


def write_current_room(path: str, room_num: str) -> None:
    """
    Write the current room number to a small file for TUI subprocess.

    :param path: File path.
    :param room_num: Current room number string.
    """
    paths.atomic_write(path, room_num)


def read_current_room(path: str) -> str:
    """
    Read the current room number from disk.

    :param path: File path written by :func:`write_current_room`.
    :returns: Room number string, or empty string if unavailable.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return ""


def write_fasttravel(path: str, steps: list[tuple[str, str]], noreply: bool = False) -> None:
    """
    Write fast travel steps to disk for the REPL to read.

    :param path: File path.
    :param steps: List of (direction, expected_room_num) pairs.
    :param noreply: If True, disable triggers during travel.
    """
    data = {"steps": steps, "noreply": noreply}
    paths.atomic_write(path, json.dumps(data))


def read_fasttravel(path: str) -> tuple[list[tuple[str, str]], bool]:
    """
    Read and delete fast travel steps from disk.

    :param path: File path written by :func:`write_fasttravel`.
    :returns: Tuple of (steps, noreply) where *steps* is a list of (direction, expected_room_num) pairs and *noreply*
        indicates whether triggers should be disabled.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        os.unlink(path)
        if isinstance(data, dict):
            steps = [(str(d), str(r)) for d, r in data.get("steps", [])]
            noreply = bool(data.get("noreply", False))
            return steps, noreply
        return [(str(d), str(r)) for d, r in data], False
    except (OSError, ValueError):
        return [], False
