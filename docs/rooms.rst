Room mapping
============

When the server sends GMCP ``Room.Info`` messages, telix builds an
incrementally-growing room graph stored in SQLite at
``$XDG_DATA_HOME/telix/rooms-<hash>.db``.

The room graph supports:

- BFS shortest-path navigation (fast travel, slow travel)
- Autodiscover (BFS-explore unvisited exits)
- Random walk (prefer rooms with unvisited exits)
- Room markers: bookmarks, blocks (excluded from pathfinding), home (one per
  area), and visual marks
- Blocked exits to prevent travel through dangerous areas
- ID rotation detection for rooms that change hash each visit

Press **F7** to open the room browser.  Rooms can be filtered by area,
sorted by name/ID/distance/last-visited, and traveled to directly.

Room browser
------------

.. include:: ../telix/help/rooms.md
   :parser: myst_parser.sphinx_
