"""Entry point for the telix CLI."""

# std imports
import sys
import argparse
import asyncio

import telnetlib3.client

# local
from . import directory, client_tui_base, client_tui_dialogs, ws_client

# Module-level store for telix-specific args, set by main() before
# telnetlib3 starts the shell.  Read by client_shell._setup_color_filter().
_color_args: argparse.Namespace | None = None

# Cached terminal background/foreground colors detected before any
# framework (Textual, telnetlib3) takes over stdin.  Set once by
# _detect_terminal_colors() in main(), read by client_shell and
# client_tui_base.
_detected_bg: tuple[int, int, int] | None = None
_detected_fg: tuple[int, int, int] | None = None


def _detect_terminal_colors() -> None:
    """
    Query the terminal for background and foreground colors.

    Must be called before Textual or telnetlib3 takes over stdin,
    otherwise the OSC 11/10 response is consumed by the framework.
    Stores results in :data:`_detected_bg` and :data:`_detected_fg`.
    """
    global _detected_bg, _detected_fg
    import blessed
    term = blessed.Terminal()
    with term.cbreak():
        bg = term.get_bgcolor(timeout=0.5, bits=8)
        fg = term.get_fgcolor(timeout=0.5, bits=8)
    _detected_bg = bg if bg != (-1, -1, -1) else None
    _detected_fg = fg if fg != (-1, -1, -1) else None


def _build_telix_parser() -> argparse.ArgumentParser:
    """
    Build argument parser for telix-specific CLI flags.

    These flags are consumed by telix and stripped from ``sys.argv``
    before telnetlib3 parses its own arguments.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--colormatch", default="vga")
    parser.add_argument("--color-brightness", type=float, default=1.0,
                        dest="color_brightness")
    parser.add_argument("--color-contrast", type=float, default=1.0,
                        dest="color_contrast")
    parser.add_argument("--background-color", default="#000000",
                        dest="background_color")
    parser.add_argument("--no-ice-colors", action="store_true", default=False,
                        dest="no_ice_colors")
    parser.add_argument("--no-repl", action="store_true", default=False,
                        dest="no_repl")
    return parser


def _strip_telix_args() -> argparse.Namespace:
    """
    Parse and remove telix-specific flags from ``sys.argv``.

    :returns: Namespace with the parsed telix-specific values.
    """
    parser = _build_telix_parser()
    telix_args, remaining = parser.parse_known_args(sys.argv[1:])
    sys.argv[1:] = remaining
    return telix_args


def reinit() -> None:
    """Overwrite sessions.json with the bundled directory."""
    sessions = directory.directory_to_sessions()
    client_tui_base.save_sessions(sessions)
    print(f"Loaded {len(sessions)} sessions from directory.")


BBS_TELNET_FLAGS = [
    "--raw-mode",
    "--colormatch", "vga",
]

MUD_TELNET_FLAGS = [
    "--line-mode",
    "--compression",
    "--colormatch", "none",
    "--no-ice-colors",
]


def pop_server_type() -> str:
    """
    Remove ``--bbs`` or ``--mud`` from ``sys.argv`` and return the type.

    :returns: ``"bbs"``, ``"mud"``, or ``""`` if neither flag was given.
    """
    for flag, value in (("--bbs", "bbs"), ("--mud", "mud")):
        if flag in sys.argv[1:]:
            sys.argv.remove(flag)
            return value
    return ""


def main() -> None:
    """
    Entry point for the ``telix`` command.

    Without arguments, launches the TUI session manager.  With a ``ws://`` or
    ``wss://`` URL, connects directly via WebSocket.  With a host argument,
    connects directly via telnetlib3's client.

    The ``--bbs`` and ``--mud`` flags apply connection presets matching the TUI
    session editor defaults for each server type.
    """
    global _color_args

    if "--reinit" in sys.argv[1:]:
        reinit()
        return

    _detect_terminal_colors()

    server_type = pop_server_type()

    has_ws_url = any(arg.startswith(("ws://", "wss://")) for arg in sys.argv[1:])

    if has_ws_url:
        parser = ws_client.build_parser()
        args = parser.parse_args()
        no_repl = args.no_repl or server_type == "bbs"
        try:
            asyncio.run(
            ws_client.run_ws_client(
                url=args.url,
                shell=args.shell,
                no_repl=no_repl,
                loglevel=args.loglevel,
                logfile=args.logfile,
                typescript=args.typescript,
                logfile_mode=args.logfile_mode,
                typescript_mode=args.typescript_mode,
            )
        )
        except KeyboardInterrupt:
            pass
        except OSError as err:
            print(f"Error: {err}", file=sys.stderr)
            sys.exit(1)
        return

    has_host = any(not arg.startswith("-") for arg in sys.argv[1:])
    wants_help = "-h" in sys.argv[1:] or "--help" in sys.argv[1:]
    if not has_host and not wants_help:
        client_tui_dialogs.tui_main()
        return

    # Apply server type presets before parsing.
    if server_type == "bbs":
        sys.argv.extend(BBS_TELNET_FLAGS)
    elif server_type == "mud":
        sys.argv.extend(MUD_TELNET_FLAGS)

    # Parse and strip telix-specific flags so telnetlib3 doesn't see them.
    telix_args = _strip_telix_args()
    _color_args = telix_args

    # Inject the telix shell so telnetlib3 uses our REPL-enabled shell.
    # --no-repl or BBS preset disables the REPL, so skip shell injection.
    if "--shell" not in sys.argv and not telix_args.no_repl and server_type != "bbs":
        sys.argv.insert(1, "--shell=telix.client_shell.telix_client_shell")

    try:
        asyncio.run(telnetlib3.client.run_client())
    except KeyboardInterrupt:
        pass
    except OSError as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
