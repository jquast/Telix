Contributing
============

We welcome contributions via GitHub pull requests:

- `Fork a Repo <https://help.github.com/articles/fork-a-repo/>`_
- `Creating a pull request
  <https://help.github.com/articles/creating-a-pull-request/>`_

Version API
-----------

This project uses `Semantic Versioning <https://semver.org/>`_ for the scripting API, Commands and
configuration files.  These are expected to be backwards-compatible; when a breaking change is
necessary, the major version is incremented.

This project does *not* follow semantic versioning for Python functions, classes, modules, or their
signatures -- **any name can change at any time**.  It is not recommended to ``import telix`` for
use in serious projects.

Architecture
------------

Telix is primarily a TUI interface for MUDs and BBSs over Telnet, WebSocket and SSH.  For MUDs,
Telix provides a nice TUI for automations and GMCP data, and "line mode" appropriate for those.  For
BBSs, it provides color correction, automatic encoding translations, and era-accurate retrocomputing
colors and fonts using modern terminal graphics protocols like kitty or sixel.

Telix uses sub-processes to launch sessions, and further, uses subprocesses to launch TUI's from the
MUD/linemode REPL. Inter-process communication is achieved by modification of shared files, like
``.current-room-<hash>`` and ``.fasttravel-<hash>``. This is a complication for developer
convenience, by launching new sub-processes, the latest code is automatically loaded without
requiring a full restart of Telix.

Dependencies
------------

textual_, blessed_, and wcwidth_ is used for the User Interface.  telnetlib3_, asyncssh_,
websockets_ for networking.  numpy_ is used for graphics rendering of retrocomputing fonts.

wcwidth_ is depended on by each of Telix, telnetlib3_, blessed_, and rich_ for string operations
related to measuring the width of strings containing sequences and complex unicode like emojis.

blessed_ is depended on for general terminal support for access to terminal sequence, feature
detection, and keyboard handling, and to provide the REPL for MUD connections. telnetlib3_ also
requires blessed on windows for keyboard support in its win32 client shell, the same client shell
integrated with by Telix.

textual_ is used for all complex TUIs, which depends on its core library rich_.  For Windows
systems, jinxed_ is used by both Telix and telnetlib3_ for msvcrt keyboard routines.  numpy_
provides vectorized image processing for the sixel and kitty graphics renderers.

File overview
-------------

The following is auto-generated as a convenience, of the first line of python docstring of each file.

.. begin-file-overview
.. code-block:: text

    __init__.py                    Telix: a TUI telnet and MUD client.
    chat.py                        Chat message persistence for GMCP ``Comm.Channel.Text``.
    client_repl.py                 REPL and TUI components for linemode telnet client sessions.
    client_repl_color.py           HSV/RGB colour math and vital-bar flash animation helpers.
    client_repl_commands.py        Command expansion, queuing, chained command sending, and macro execution.
    client_repl_dialogs.py         TUI thread-based management: confirmation dialogs, help screen, editor launchers.
    client_repl_render.py          Vital-bar rendering, toolbar layout, and display helpers.
    client_repl_sextant.py         Sextant block character table and password scrambling.
    client_repl_travel.py          Movement and pathfinding: travel, autodiscover, randomwalk.
    client_shell.py                Telix client shell -- wraps telnetlib3's terminal handling with REPL support.
    client_tui.py                  Textual TUI session manager for telix -- re-export hub.
    client_tui_app.py              Main Textual application and entry point for the telix TUI.
    client_tui_bars.py             Progress bar and theme editor panes and screens for the telix TUI.
    client_tui_base.py             Foundation layer for the Textual TUI editor infrastructure.
    client_tui_captures.py         Highlight captures and chat viewer screens for the telix TUI.
    client_tui_dialogs.py          Confirmation dialogs, walk dialogs, and the tabbed editor screen.
    client_tui_editors.py          Standalone entry points for the Textual TUI editor panes.
    client_tui_highlights.py       Highlight editor pane and screen for the telix TUI.
    client_tui_macros.py           Macro editor pane and screen for the telix TUI.
    client_tui_rooms.py            Room browser, picker, and graph editor screens for the telix TUI.
    client_tui_session_manager.py  Session management layer for the Textual TUI.
    client_tui_triggers.py         Trigger editor pane and screen for the telix TUI.
    color_filter.py                ANSI color palette translation for MUD/BBS client output.
    directory.py                   Load the bundled MUD/BBS directory and convert to session configs.
    fonts/font_registry.py         Bitmap font registry -- auto-generated by tools/build_fonts.py.
    gmcp_snapshot.py               Rolling GMCP data snapshot persistence.
    graphics_renderer.py           Sixel and Kitty graphics protocol encoders.
    graphics_writer.py             Graphics writer: pyte virtual terminal rendered via sixel/kitty graphics.
    highlighter.py                 Output text highlighting engine for MUD client sessions.
    macros.py                      Macro key binding support for the REPL client.
    main.py                        Entry point for the telix CLI.
    mslp.py                        MSLP (Mud Server Link Protocol) keyboard navigation.
    mtts.py                        MTTS and MNES protocol support.
    paths.py                       Consolidated XDG Base Directory paths for telix.
    progressbars.py                Progress bar configuration model for the GMCP vitals toolbar.
    repl_theme.py                  Resolve the user's Textual theme into concrete hex colors for the blessed REPL.
    rooms.py                       Room graph tracking, BFS pathfinding, and SQLite persistence for GMCP Room.Info data.
    scripts.py                     Async Python scripting engine for telix.
    session_context.py             Per-connection session state for MUD client sessions.
    ssh_client.py                  SSH client for telix.
    ssh_transport.py               SSH reader/writer adapters for telix sessions.
    terminal.py                    Platform dispatcher for terminal operations.
    terminal_unix.py               Unix-specific terminal operations for the telix REPL.
    terminal_win32.py              Windows terminal operations for the telix REPL (stubs).
    trigger.py                     Server output pattern matching and automatic reply engine.
    util.py                        Small shared utility functions.
    ws_client.py                   WebSocket client for telix.
    ws_transport.py                WebSocket reader/writer adapters for MUD client sessions.

.. end-file-overview

Developing
----------

Development requires Python 3.10+.  Install in editable mode::

    pip install -e .

Any changes made in this project folder are then made available to the Python interpreter as the
``telix`` CLI command and python module regardless of the current working directory.

Running tests
~~~~~~~~~~~~~

pytest_ is the test runner.  Install and run using tox::

    pip install --upgrade tox
    tox

Run a single test file::

    tox -e py314 -- telix/tests/test_chat.py -x -v

Code formatting
~~~~~~~~~~~~~~~

This project uses ruff_ for code formatting and linting::

    tox -e format

You can also set up a `pre-commit <https://pre-commit.com/>`_ hook::

    pip install pre-commit
    pre-commit install --install-hooks

Run all linters::

    tox -e lint

Run individual linters::

    tox -e ruff
    tox -e ruff_format
    tox -e pydocstyle
    tox -e codespell

Style
-----

.. include:: ../.claude/CLAUDE.md
   :parser: myst
   :start-after: ## Style and static analysis
   :end-before: ## Development workflow

Workflow
--------

.. include:: ../.claude/CLAUDE.md
   :parser: myst
   :start-after: ## Development workflow

Integration boundaries
----------------------

``telix.main`` is the single CLI entry point.  It inspects the first
positional argument and routes to one of three paths:

- **No argument** -- launches the Textual TUI session manager.
- **``ws://`` or ``wss://`` URL** -- parses WS-specific flags via
  ``ws_client.build_parser()`` and calls ``ws_client.run_ws_client()``,
  which connects via ``websockets.connect()`` using the
  ``gmcp.mudstandards.org`` subprotocol and invokes
  ``ws_client_shell(reader, writer)`` with
  ``WebSocketReader``/``WebSocketWriter`` adapters.
- **Plain host** -- injects ``--shell=telix.client_shell.telix_client_shell``
  into ``sys.argv`` and calls ``telnetlib3.client.run_client()``, which
  parses all remaining CLI arguments and opens the Telnet connection.  The
  shell is a drop-in replacement for
  ``telnetlib3.client_shell.telnet_client_shell``.

Before routing, ``main()`` checks for ``--bbs`` or ``--mud`` and removes
the flag from ``sys.argv``.  The flag injects preset arguments
(``BBS_TELNET_FLAGS`` / ``MUD_TELNET_FLAGS``) that mirror the TUI session
editor presets.  For BBS, the telix shell is not injected (REPL disabled);
for WebSocket connections, ``--bbs`` sets ``no_repl=True``.

The TUI launches connection subprocesses via ``subprocess.Popen``.  Both
transports use the same ``python -c "from telix.main import main; main()"``
invocation -- the URL or host argument in the subprocess command determines
which path ``main`` takes.

Every ``TelnetWriter`` (or ``WebSocketWriter``) has a ``.ctx``
attribute that defaults to a ``TelnetSessionContext``.  Telix's
``SessionContext`` subclasses ``TelnetSessionContext``, adding
MUD-specific state (rooms, macros, highlights, chat, etc.).  The
shell callback creates a ``SessionContext`` and assigns it to
``writer.ctx``.

Telix's ``SessionContext`` also provides ``captures`` (a flat
``dict[str, int]`` of captured variables) and ``capture_log`` (a
``dict[str, list[dict]]`` of per-channel capture history), populated
by the highlight engine and consumed by the ``when`` condition checker
and the Capture Window (Alt+C).

``TelnetSessionContext`` (defined in ``telnetlib3/session_context.py``)
provides the attributes that ``telnetlib3.client_shell`` uses:

- ``color_filter`` -- object with ``.filter(str) -> str``
- ``raw_mode`` -- ``None`` (auto-detect), ``True``, or ``False``
- ``ascii_eol`` -- ``bool``
- ``input_filter`` -- ``InputFilter`` or ``None``
- ``trigger_engine`` -- trigger engine or ``None``
- ``trigger_wait_fn`` -- async callable or ``None``
- ``typescript_file`` -- open file handle or ``None``
- ``gmcp_data`` -- ``dict[str, Any]`` of raw GMCP package data

GMCP data flow
~~~~~~~~~~~~~~

GMCP (Generic MUD Communication Protocol) data arrives as telnet
sub-negotiation and is parsed by telnetlib3_ into package/data pairs.
``TelnetClient.on_gmcp()`` stores each package in ``ctx.gmcp_data``
(merging dict updates for the same package key).

Telix overrides the GMCP ext callback in ``telix_client_shell`` to
wrap the base ``on_gmcp`` with package-specific dispatch to callbacks
on ``SessionContext``:

- ``on_chat_text`` -- called for ``Comm.Channel.Text``
- ``on_chat_channels`` -- called for ``Comm.Channel.List``
- ``on_room_info`` -- called for ``Room.Info``

These callback attributes are defined on Telix's ``SessionContext``
and wired up in ``client_shell.load_configs()``.  Access them as
regular attributes -- do not use ``getattr()``.

Room tracking
~~~~~~~~~~~~~

Room state lives in two parallel systems:

1. **In-memory** (for REPL commands like randomwalk, autodiscover,
   and fast-travel): ``ctx.room.current``,
   ``ctx.room.previous``, ``ctx.room.changed``, and
   ``ctx.room.graph`` (a ``RoomStore`` backed by a SQLite database
   at ``ctx.room.file``).

2. **File-based** (for TUI subprocesses like the Alt+R room browser):
   ``ctx.room.current_file`` contains the current room number as
   plain text, read by ``rooms.read_current_room()``.  The rooms
   SQLite DB is shared between both systems.

The ``on_room_info`` callback bridges these: when a ``Room.Info``
GMCP message arrives, it updates ``ctx.room.current``, calls
``room_graph.update_room()`` to persist the room and its exits to
SQLite, and writes ``ctx.room.current_file`` so TUI subprocesses
see the change.

TUI editor subprocesses
~~~~~~~~~~~~~~~~~~~~~~~

Pressing editor keys (Alt+H, Alt+M, Alt+A, etc.) launches Textual-based editor screens in a
**child subprocess** via ``launch_tui_editor()`` in
``client_repl_dialogs.py``.  Key constraints:

- **Never pipe stderr** (``stderr=subprocess.PIPE``).  Textual
  renders its TUI to stderr.  Piping it redirects Textual's output
  to a pipe instead of the terminal, freezing the app because stderr
  is no longer a TTY.

- **Error display**.  Textual stores unhandled exceptions in
  ``app._exception`` and queues Rich tracebacks in
  ``app._exit_renderables``.  In non-pilot mode Textual never calls
  ``print_error_renderables()`` itself, so ``EditorApp`` overrides
  it to write to stdout (not stderr) after the alt screen exits.
  ``run_editor_app()`` calls it explicitly on non-zero return codes.

- **Blocking fds**.  The parent's asyncio event loop sets stdin
  non-blocking.  Since stdin/stdout/stderr share the same PTY file
  descriptor, the child inherits non-blocking mode.
  ``restore_blocking_fds()`` must run before Textual starts.

- **In-band resize (DEC mode 2048)**.  The REPL enables DEC private
  mode 2048 so the terminal sends resize notifications as escape
  sequences instead of (or in addition to) SIGWINCH.  Textual also
  supports this mode and disables it on
  ``stop_application_mode()``.
  ``restore_after_subprocess()`` must NOT re-enable mode 2048
  immediately -- the terminal responds with a resize notification
  that arrives before the REPL event loop is ready, causing a storm
  of redundant full-screen repaints.  Instead, the module-level
  flag ``subprocess_needs_rearm`` is set, and the main event loop
  calls ``rearm_after_subprocess()`` after the post-action render
  is complete.  That method flushes stale terminal input
  (``termios.tcflush``), records the current terminal size (to
  suppress ``on_resize_repaint``), and only then re-enables
  mode 2048.

- **Traceback display**.  ``run_editor_app()`` wraps the Textual
  ``app.run()`` call.  On crash it writes ``TERMINAL_CLEANUP``
  (which includes cursor-home and clear-screen) and calls
  ``restore_opost()`` to re-enable the terminal's ``OPOST`` flag
  so ``\n`` maps to ``\r\n`` -- without this, tracebacks render
  with staircase output because the terminal is still in raw mode.

REPL output pipeline
--------------------

The REPL reads server data in ``read_server`` (``client_repl.py``)
using ``await telnet_reader.read()``.  Incoming text flows through
several stages before reaching the terminal:

1. **Telnet parsing** -- ``telnetlib3`` strips IAC sequences and
   decodes bytes to text.  IAC-only segments produce no data; the
   reader stays blocked.

2. **Output transform** -- ``transform_output()`` normalises
   line endings and applies the color filter.

3. **Line hold** -- ``LineHoldBuffer.add(text)`` splits the text
   at the last ``\n``.  Complete lines go to ``emit_now``; the
   trailing fragment (e.g. a prompt without ``\n``) is held back.
   ``schedule_line_hold_flush()`` starts a 150 ms debounce timer
   (``LINE_HOLD_TIMEOUT``).

4. **Prompt signal** -- If the server sends IAC GA or IAC EOR, the
   ``on_prompt_signal`` callback sets ``prompt_pending = True``.
   The main loop flushes held text immediately when it sees a
   pending prompt (``flush_for_prompt``).

5. **Highlight engine** -- ``emit_now`` lines are run through the
   highlight engine before display; held-back text flushed by the
   timer is written raw (no highlights).  Rules with ``captured=True``
   extract regex groups into ``ctx.captures`` (for ``when`` conditions)
   and log matched lines to ``ctx.capture_log`` (for the Capture
   Window).

6. **Screen output** -- The REPL saves/restores the cursor position
   via VT100 DECSC (``\x1b7``) / DECRC (``\x1b8``), writes to
   ``stdout`` (an ``asyncio.StreamWriter`` connected to the PTY
   master FD via ``connect_write_pipe``), and re-renders the input
   line and toolbar after each write.

7. **Scroll region** -- ``ScrollRegion`` confines server output to
   the top portion of the terminal using DECSTBM
   (``change_scroll_region``).  The input line and toolbar sit
   below the scroll boundary.

   ``grow_reserve()`` expands the reserved area when the GMCP
   toolbar first appears.  It scrolls existing content up by
   emitting newlines at the scroll-region bottom, then adjusts
   the saved cursor position by the same amount so that subsequent
   restore/save pairs stay consistent.

Connection lifecycle
~~~~~~~~~~~~~~~~~~~~

The shell callback (``client_shell.py``) drives the outer
REPL/raw-mode loop:

1. ``telix_client_shell`` is called by telnetlib3 after connection.
2. ``want_repl()`` decides the mode (line vs. kludge/raw).
3. ``repl_event_loop`` sets up the scroll region, registers IAC
   callbacks, and starts ``read_server`` + ``read_input`` as
   concurrent tasks via ``run_repl_tasks``.
4. When the server switches to kludge mode or the connection
   closes, the REPL returns and the outer loop re-evaluates.

Data arriving **before** the REPL event loop starts is buffered in
the telnet reader's internal buffer and consumed by the first
``read()`` call in ``read_server``.



.. _telnetlib3: https://github.com/jquast/telnetlib3
.. _blessed: https://github.com/jquast/blessed
.. _wcwidth: https://github.com/jquast/wcwidth
.. _textual: https://github.com/Textualize/textual
.. _rich: https://github.com/Textualize/rich
.. _pytest: https://pytest.org
.. _ruff: https://docs.astral.sh/ruff/
.. _asyncssh: https://asyncssh.readthedocs.io/
.. _websockets: https://websockets.readthedocs.io/
.. _numpy: https://numpy.org/
.. _jinxed: https://github.com/rockhopper-Technologies/jinxed
