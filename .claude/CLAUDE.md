# telix

TUI telnet and MUD client built on top of telnetlib3.

- `docs/intro.rst`: overview, installation, usage
- `docs/session-manager.rst`: TUI session manager
- `docs/contributing.rst`: architecture, integration boundary, development
- `docs/files.rst`: config file paths, XDG layout
- `telix/help/`: user-facing help (commands, macros, triggers, highlights, rooms)
- `.editorconfig`: defines basic formatting

## Code file overview

```
telix/
  main.py                 CLI entry (TUI or direct connect)
  session_context.py      Per-connection mutable state
  client_shell.py         Shell callback (drop-in for telnetlib3)

  ws_transport.py         WebSocket reader/writer adapters
  ws_client.py            WebSocket connection core (run_ws_client, build_parser)

  ssh_transport.py        SSH reader/writer adapters
  ssh_client.py           SSH connection core (run_ssh_client)

  scripts.py              Async Python scripting engine

  client_repl.py          blessed LineEditor REPL event loop
  client_repl_render.py   Toolbar / status line rendering
  client_repl_commands.py Command expansion and backtick dispatch
  client_repl_dialogs.py  Interactive dialogs (confirm, input)
  client_repl_travel.py   Room graph navigation
  repl_theme.py           Textual theme to REPL palette resolution

  client_tui.py           Re-export hub (backwards compat)
  client_tui_base.py      TUI foundation: sessions, base editors, app
  client_tui_editors.py   Re-export hub (backwards compat)
  client_tui_app.py       Textual app entry point and session launcher
  client_tui_macros.py    Macro editor
  client_tui_triggers.py  Trigger editor
  client_tui_highlights.py Highlight editor
  client_tui_bars.py      Progress bar editor
  client_tui_rooms.py     Room browser TUI
  client_tui_captures.py  Capture window (Alt+C)
  client_tui_dialogs.py   Confirmation dialogs, walk dialogs, tabbed editor

  trigger.py              Pattern-triggered automatic responses
  macros.py               Key-bound macro definitions
  highlighter.py          Regex-based output highlighting + captures
  rooms.py                GMCP Room.Info graph store (SQLite)
  chat.py                 GMCP Comm.Channel.Text persistence
  directory.py            Bundled MUD/BBS directory loader
  progressbars.py         Progress bar config loading/saving
  gmcp_snapshot.py        GMCP snapshot persistence

  color_filter.py         ANSI/PETSCII/ATASCII color palette translation
  terminal.py             Terminal abstraction (Unix/Win32 dispatch)
  terminal_unix.py        Unix terminal operations (termios, PTY)
  terminal_win32.py       Windows terminal operations (msvcrt)
  mtts.py                 MTTS terminal type standard bitvector
  mslp.py                 MSLP multiline softlink parser

  paths.py                XDG base directory resolution
  util.py                 Small internal helpers
  help/                   Markdown help files loaded at runtime
```

## Style and static analysis

- Do not use `getattr(obj, "attr", default)` as defensive noise when the attribute is always
  present.  If the call site owns the invariant, access it directly as `obj.attr`.
- Do not use single-underscore prefixes on names (functions, classes, constants, methods, or
  attributes).  This project has no public Python API -- all names are internal.  Exceptions:
  - Unused variables in unpacking (e.g. `for _s, _e, name in spans:`)
  - Property backing attributes (e.g. `self._enabled` behind `@property enabled`)
  - External library private attributes (e.g. `widget._label`, `parser._actions`)
- Import style: `import module` everywhere, access via `module.name`.  Internal imports use
  `from . import module`.  Never `from X import Y` except `from typing import TYPE_CHECKING`
  and inside `if TYPE_CHECKING:` blocks.
- Omit type annotations rather than use ambiguous types or `# type: ignore`.  Tests must not use
  type annotations; tests are excluded from type checking.
- Do not write Unicode em-dash, arrows, or similar characters in code or documentation.
- Use tox to run tests, linters, and formatters.
- Max line length: 120 characters.
- Sphinx-style reStructuredText docstrings.
- TUI and REPL modules should have basic coverage for data-handling and validation logic;
  only interactive rendering and layout is excluded.
- Write tests first when fixing bugs (TDD).
- Do not use section dividers or markers in code.
- Tests should be self-documenting: no assertion messages, no explanatory comments, no description
  parameters in parametrized tests.  Docstrings should be brief factual statements.
- Do not write defensive `try`/`except` blocks that swallow errors.  Let exceptions propagate
  unless there is a specific reason to handle them.  Never catch broad `Exception` or `OSError`
  just to log and return `None`.  Acceptable uses: `except ImportError` for optional
  dependencies, cleanup in `finally` blocks, and boundary code that must not crash (e.g. top-level
  CLI).

## Development workflow

- Review whether tests can be simplified: join related tests, use parametrized testing, and reduce
  line count while keeping the same coverage.
- After larger changes, review for unnecessary complexity: reduce duplication, use walrus operators
  or context managers, and lower McCabe complexity.
