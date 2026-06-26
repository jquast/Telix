Files and directories
=====================

All persistent state follows the `XDG Base Directory Specification
<https://specifications.freedesktop.org/basedir-spec/latest/>`_.  Override
locations with ``$XDG_CONFIG_HOME`` and ``$XDG_DATA_HOME``.

Common defaults:

.. list-table::
   :header-rows: 1

   * - Variable
     - Linux
     - macOS
     - Windows
   * - ``$XDG_CONFIG_HOME``
     - ``~/.config``
     - ``~/Library/Application Support``
     - ``%APPDATA%``
   * - ``$XDG_DATA_HOME``
     - ``~/.local/share``
     - ``~/Library/Application Support``
     - ``%LOCALAPPDATA%``

``$XDG_CONFIG_HOME/telix/`` contains Telix configuration:

- ``sessions.json``
- ``triggers.json``
- ``macros.json``
- ``highlights.json``
- ``progressbars.json`` - progress bar toolbar configuration

``$XDG_DATA_HOME/telix/`` contains per-session data using a SHA-256 slug
of ``host:port`` as ``<hash>``:

- ``history-<hash>`` - command history
- ``rooms-<hash>.db`` - SQLite room graph (GMCP Room.Info)
- ``chat-<hash>.json`` - GMCP Comm.Channel.Text history
- ``gmcp-<hash>.json`` - rolling GMCP data snapshot with per-package timestamps
- ``prefs-<hash>.json`` - per-session runtime preferences
- ``.current-room-<hash>`` - current room number (read by the room browser)
- ``.fasttravel-<hash>`` - fast-travel steps (written by the room browser)

Bundled with the package:

- ``telix/data/favorites.ini`` - curated server list with presets (mode, encoding, echo mode, etc.)
- ``telix/data/directory.json`` - community MUD/BBS directory (from jquast/modem.xyz project)
- ``telix/fonts/fonts.bin`` - bitmap font archive (45 fonts, 8x8 to 8x16, ~174 KB)
- ``telix/fonts/font_registry.py`` - font ID, name, encoding, and binary offset mapping
