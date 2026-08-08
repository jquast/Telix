"""
Raw TCP client for telix.

Provides :func:`run_raw_client`, :func:`build_parser`, and :func:`main`, called by the ``telix-raw`` entry point.
Connects via ``asyncio.open_connection()`` and runs the raw event loop through
:func:`~telix.client_shell.raw_client_shell`.

No telnet negotiation is performed; data is transmitted as raw bytes over the TCP socket without IAC interpretation.
"""

import shutil
import typing
import asyncio
import logging
import argparse
from collections.abc import Callable, Awaitable

from . import raw_transport
from .telix_config import TelixConfig

log = logging.getLogger(__name__)

_LEVEL_MAP = {
    "trace": 5,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


async def run_raw_client(
    host: str,
    port: int,
    shell: Callable[..., Awaitable[None]],
    color_args: "TelixConfig | None" = None,
    encoding: str = "utf-8",
    encoding_errors: str = "replace",
    typescript: str = "",
    typescript_mode: str = "append",
    ansi_keys: bool = False,
    ascii_eol: bool = False,
) -> None:
    """
    Connect to a raw TCP server and run the telix shell.

    Creates :class:`~telix.raw_transport.RawReader` and :class:`~telix.raw_transport.RawWriter` adapters, starts the
    shell as a background task, then opens a raw TCP connection.  Data received from the server is decoded and fed into
    the reader queue; EOF is signalled on disconnect.

    :param host: Server hostname or address.
    :param port: TCP port (default 23).
    :param shell: Async callable shell(reader, writer) -- the event loop entry point.
    :param color_args: Parsed color/palette CLI options, or None to skip color setup.
    :param encoding: Wire byte decoding (e.g. "utf-8", "iso-8859-1").
    :param encoding_errors: Error handling for the wire decoder (default "replace").
    :param typescript: Path to typescript recording file, or empty string.
    :param ansi_keys: Enable ANSI escape key sequences.
    """
    reader = raw_transport.RawReader()
    writer = raw_transport.RawWriter(peername=(host, port))
    writer.color_args = color_args  # type: ignore[attr-defined]
    writer.encoding = encoding
    writer.encoding_errors = encoding_errors
    writer.typescript = typescript  # type: ignore[attr-defined]
    writer.typescript_mode = typescript_mode  # type: ignore[attr-defined]
    writer.ascii_eol = ascii_eol  # type: ignore[attr-defined]

    shell_task = asyncio.ensure_future(shell(reader, writer))

    cols, rows = shutil.get_terminal_size()

    if color_args is not None:
        if color_args.graphics_font:
            pass

    try:
        socket_reader, socket_writer = await asyncio.open_connection(host, port)
        writer.stream_writer = socket_writer
        try:
            while True:
                data = await socket_reader.read(4096)
                if not data:
                    break
                text = data.decode(encoding, errors=encoding_errors)
                reader.feed_data(text)
        finally:
            reader.feed_eof()
    finally:
        writer.close()

    await shell_task


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``telix-raw`` entry point."""
    parser = argparse.ArgumentParser(prog="telix", description="Raw TCP connection (internal subprocess)")
    parser.add_argument("host", help="TCP server hostname")

    conn = parser.add_argument_group("Connection options")
    conn.add_argument("--port", type=int, default=23, metavar="N", help="TCP port (default: 23)")
    conn.add_argument(
        "--loglevel",
        default="warn",
        choices=["trace", "debug", "info", "warn", "error", "critical"],
        help="logging level (default: warn)",
    )
    conn.add_argument("--logfile", default="", metavar="FILE", help="write log to FILE")
    conn.add_argument(
        "--logfile-mode",
        default="append",
        choices=["append", "rewrite"],
        help="log file write mode: append (default) or rewrite",
    )
    conn.add_argument("--typescript", default="", metavar="FILE", help="record session to FILE")
    conn.add_argument(
        "--typescript-mode",
        default="append",
        choices=["append", "rewrite"],
        help="typescript write mode: append (default) or rewrite",
    )
    conn.add_argument("--encoding", default="utf-8", metavar="ENC", help="connection encoding (default: utf-8)")
    conn.add_argument(
        "--encoding-errors", default="replace", metavar="POLICY", help="encoding error handling (default: replace)"
    )

    telix = parser.add_argument_group("Telix options")
    telix.add_argument(
        "--colormatch", default="vga", metavar="PALETTE", help="color palette for remapping (default: vga)"
    )
    telix.add_argument(
        "--no-ice-colors",
        action="store_true",
        default=False,
        dest="no_ice_colors",
        help="disable iCE color (blink as bright background) support",
    )
    telix.add_argument(
        "--background-color",
        default="#000000",
        dest="background_color",
        metavar="COLOR",
        help="terminal background color as #RRGGBB (default: #000000)",
    )
    telix.add_argument(
        "--color-brightness",
        type=float,
        default=1.0,
        dest="color_brightness",
        metavar="N",
        help="color brightness multiplier (default: 1.0)",
    )
    telix.add_argument(
        "--color-contrast",
        type=float,
        default=1.0,
        dest="color_contrast",
        metavar="N",
        help="color contrast multiplier (default: 1.0)",
    )
    telix.add_argument("--ansi-keys", action="store_true", default=False, dest="ansi_keys")
    telix.add_argument("--ascii-eol", action="store_true", default=False, dest="ascii_eol")
    telix.add_argument("--clear-homes-cursor", action="store_true", default=False, dest="clear_homes_cursor")
    telix.add_argument("--ff-clears-screen", action="store_true", default=False, dest="ff_clears_screen")
    telix.add_argument("--graphics-font", nargs="?", const="auto", default="", dest="graphics_font")
    telix.add_argument("--graphics-columns", type=int, default=None, dest="graphics_columns")
    telix.add_argument("--graphics-rows", type=int, default=None, dest="graphics_rows")
    telix.add_argument(
        "--font-id", type=int, default=None, dest="font_id", help="font id for graphics rendering (default: 0)"
    )
    telix.add_argument("--local-echo", action="store_true", default=False, dest="local_echo", help="force local echo")
    telix.add_argument(
        "--remote-echo", action="store_true", default=False, dest="remote_echo", help="force remote echo"
    )
    return parser


async def raw_client_shell(raw_reader: raw_transport.RawReader, raw_writer: raw_transport.RawWriter) -> None:
    """
    Telix client shell for raw TCP connections.

    Raw TCP connections are always BBS/raw mode -- no telnet negotiation occurs, so there is no REPL line-mode
    switching.  Creates a :class:`~telix.session_context.TelixSessionContext`, loads configs, and runs a single raw
    event-loop pass.

    :param raw_reader: :class:`~telix.raw_transport.RawReader` fed by the receive loop.
    :param raw_writer: :class:`~telix.raw_transport.RawWriter` wrapping the raw TCP socket.
    """
    import telnetlib3.accessories
    import telnetlib3.client_shell
    import telnetlib3._session_context

    from . import session_context
    from .client_shell import (
        load_configs,
        setup_font_id,
        make_raw_stdout,
        build_session_key,
        setup_clear_homes,
        setup_color_filter,
        setup_graphics_font,
    )

    raw_writer.ctx = telnetlib3._session_context.TelnetSessionContext()  # type: ignore[assignment]
    raw_writer.ctx.color_args = getattr(raw_writer, "color_args", None)

    ctx = raw_writer.ctx = session_context.TelixSessionContext.create_using_telnet_ctx(
        session_key=build_session_key(raw_writer),  # type: ignore[arg-type]
        writer=raw_writer,  # type: ignore[arg-type]
        encoding=raw_writer.encoding,
    )
    ctx.repl.enabled = False
    ctx.raw_mode = True
    ctx.ascii_eol = getattr(raw_writer, "ascii_eol", False)

    typescript = getattr(raw_writer, "typescript", "")
    typescript_mode = getattr(raw_writer, "typescript_mode", "append")
    if typescript:
        ctx.typescript_file = open(typescript, "w" if typescript_mode == "rewrite" else "a", encoding="utf-8")

    load_configs(ctx)
    setup_color_filter(ctx, raw_writer)  # type: ignore[arg-type]
    setup_clear_homes(ctx)
    setup_graphics_font(ctx)
    setup_font_id(ctx)

    ansi_keys = getattr(raw_writer, "ansi_keys", False)
    if ansi_keys:
        ctx.repl.ansi_keys = True

    keyboard_escape = ctx.repl.keyboard_escape

    with telnetlib3.client_shell.Terminal(telnet_writer=raw_writer) as tty_shell:  # type: ignore[arg-type]
        linesep = "\r\n"
        stdout = await tty_shell.make_stdout()
        tty_shell.setup_winch()

        escape_name = telnetlib3.accessories.name_unicode(keyboard_escape)
        stdout.write(f"Escape character is '{escape_name}'.{linesep}".encode())

        raw_stdout_ref: list[typing.Any] = [None]

        def handle_close(msg: str) -> None:
            cf = ctx.repl.color_filter
            if cf is not None:
                flush = cf.flush()
                if flush:
                    stdout.write(flush.encode())
            rs = raw_stdout_ref[0]
            if rs is not None:
                rs.write(f"\r\n{msg}\r\n".encode())
            tty_shell.cleanup_winch()

        if tty_shell._istty:
            if tty_shell._save_mode is not None:
                tty_shell.set_mode(tty_shell._make_raw(tty_shell._save_mode, suppress_echo=True))
            stdin = await tty_shell.connect_stdin()
            from .client_shell import _apply_input_xlat

            _apply_input_xlat(stdin, raw_writer.encoding, ctx.repl.ansi_keys)
            state = telnetlib3.client_shell._RawLoopState(
                switched_to_raw=True, last_will_echo=False, local_echo=False, linesep=linesep
            )
            raw_stdout = make_raw_stdout(stdout, ctx, tty_shell=tty_shell, writer=raw_writer)
            raw_stdout_ref[0] = raw_stdout
            await telnetlib3.client_shell._raw_event_loop(
                raw_reader,  # type: ignore[arg-type]
                raw_writer,  # type: ignore[arg-type]
                tty_shell,
                stdin,
                raw_stdout,
                keyboard_escape,
                state,
                handle_close,
                lambda: False,  # never switch back to REPL
            )
            tty_shell.disconnect_stdin(stdin)
        else:
            handle_close("Connection closed.")
    ctx.close()


def main() -> None:
    """Entry point for the ``telix-raw`` command."""
    import sys

    parser = build_parser()
    args = parser.parse_args()

    color_args = TelixConfig(
        colormatch=args.colormatch,
        color_brightness=args.color_brightness,
        color_contrast=args.color_contrast,
        background_color=args.background_color,
        no_ice_colors=args.no_ice_colors,
        ansi_keys=args.ansi_keys,
        clear_homes_cursor=args.clear_homes_cursor,
        ff_clears_screen=args.ff_clears_screen,
        graphics_font=args.graphics_font,
        graphics_columns=args.graphics_columns,
        graphics_rows=args.graphics_rows,
        font_id=args.font_id,
        no_repl=False,
        echo_mode="remote" if args.remote_echo else ("local" if args.local_echo else "auto"),
    )

    level = _LEVEL_MAP[args.loglevel]
    if args.logfile:
        logging.basicConfig(
            level=level,
            filename=args.logfile,
            filemode="w" if args.logfile_mode == "rewrite" else "a",
            format="%(levelname)s %(filename)s:%(lineno)d %(message)s",
        )
    else:
        logging.basicConfig(level=level, format="%(levelname)s %(filename)s:%(lineno)d %(message)s")

    asyncio.run(
        run_raw_client(
            host=args.host,
            port=args.port,
            shell=raw_client_shell,
            color_args=color_args,
            encoding=args.encoding,
            encoding_errors=args.encoding_errors,
            typescript=args.typescript,
            typescript_mode=args.typescript_mode,
            ansi_keys=args.ansi_keys,
            ascii_eol=args.ascii_eol,
        )
    )
    sys.exit(0)
