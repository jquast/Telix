"""
Telix client shell -- wraps telnetlib3's terminal handling with REPL support.

Provides :func:`telix_client_shell`, a drop-in replacement for
:func:`telnetlib3.client_shell.telnet_client_shell` that creates a
:class:`~telix.session_context.SessionContext`, loads per-session configs
(macros, triggers, highlights, chat, rooms), and alternates between
REPL and raw event loops based on telnet negotiation state.

Also provides :func:`ws_client_shell` for WebSocket connections using
the ``gmcp.mudstandards.org`` wire format.
"""

# std imports
import io
import os
import re
import sys
import shlex
import codecs
import typing
import asyncio
import logging
import contextlib

# 3rd party
import telnetlib3
import telnetlib3.client
import telnetlib3.telopt
import telnetlib3.client_shell
import telnetlib3.stream_reader
import telnetlib3.stream_writer

# local
from . import (
    chat,
    util,
    paths,
    rooms,
    macros,
    trigger,
    terminal,
    client_repl,
    highlighter,
    progressbars,
    ws_transport,
    ssh_transport,
    session_context,
)

log = logging.getLogger(__name__)

__all__ = ("ssh_client_shell", "telix_client_shell", "ws_client_shell")


def compute_local_echo(echo_mode: str, will_echo: bool) -> bool:
    """
    Compute local echo flag from echo mode and server's WILL ECHO state.

    :param echo_mode: ``"auto"``, ``"local"``, or ``"remote"``
    :param will_echo: ``True`` when server has negotiated WILL ECHO
    :returns: ``True`` if client should echo input locally
    """
    if echo_mode == "local":
        return True
    if echo_mode == "remote":
        return False
    if echo_mode == "auto":
        return False


def _apply_delete_to_backspace(stdin: typing.Any) -> None:
    """
    Patch stdin so Delete (0x7f) is sent as Backspace (0x08).

    BBS systems expect Backspace, but modern terminals send Delete in raw mode.
    """
    _real_read = stdin.read

    async def _delete_to_backspace(n: int = -1) -> bytes:
        data = await _real_read(n)
        return data.replace(b"\x7f", b"\x08")

    stdin.read = _delete_to_backspace  # type: ignore[method-assign]


def load_configs(ctx: "session_context.TelixSessionContext") -> None:
    """
    Create config/data directories and load all per-session config files into *ctx*.

    Handles macros, triggers, highlights, progress bars, GMCP snapshot, chat, rooms, and history.  Missing files are
    silently skipped so first-time connections start with empty defaults.

    :param ctx: Session context to populate.
    """
    config_dir = str(paths.xdg_config_dir())
    os.makedirs(config_dir, exist_ok=True)
    os.makedirs(str(paths.xdg_data_dir()), exist_ok=True)

    macros_path = os.path.join(config_dir, "macros.json")
    ctx.macros.file = macros_path
    if os.path.isfile(macros_path):
        ctx.macros.defs = macros.load_macros(macros_path, ctx.session_key)
    ctx.macros.defs = macros.ensure_builtin_macros(ctx.macros.defs)

    disconnect = next((m for m in ctx.macros.defs if m.builtin_name == "disconnect" and m.enabled), None)
    if disconnect is not None:
        seq = macros.key_name_to_seq(disconnect.key)
        if seq is not None:
            ctx.repl.keyboard_escape = seq

    triggers_path = os.path.join(config_dir, "triggers.json")
    ctx.triggers.file = triggers_path
    if os.path.isfile(triggers_path):
        ctx.triggers.rules = trigger.load_triggers(triggers_path, ctx.session_key)

    highlights_path = os.path.join(config_dir, "highlights.json")
    ctx.highlights.file = highlights_path
    if os.path.isfile(highlights_path):
        ctx.highlights.rules = highlighter.load_highlights(highlights_path, ctx.session_key)

    progressbars_path = os.path.join(config_dir, "progressbars.json")
    ctx.progress.file = progressbars_path
    if os.path.isfile(progressbars_path):
        ctx.progress.configs = progressbars.load_progressbars(progressbars_path, ctx.session_key)

    ctx.gmcp.snapshot_file = paths.gmcp_snapshot_path(ctx.session_key)

    chat_file = paths.chat_path(ctx.session_key)
    ctx.chat.file = chat_file
    if os.path.isfile(chat_file):
        ctx.chat.messages = chat.load_chat(chat_file)
    ctx.chat.on_text = lambda data: chat.append_chat_msg(ctx, data)
    ctx.chat.on_channels = lambda data: setattr(ctx.chat, "chat_channels", data)

    rooms_file = rooms.rooms_path(ctx.session_key)
    ctx.room.file = rooms_file
    ctx.room.current_file = rooms.current_room_path(ctx.session_key)
    ctx.room.graph = rooms.RoomStore(rooms_file)

    def on_room_info(data: typing.Any) -> None:
        num = rooms.room_id(data)
        if num is None:
            return
        ctx.room.previous = ctx.room.current
        ctx.room.current = num
        ctx.room.changed.set()
        ctx.room.changed.clear()
        ctx.room.graph.update_room(data)
        rooms.write_current_room(ctx.room.current_file, num)

    ctx.gmcp.on_room_info = on_room_info
    ctx.repl.history_file = paths.history_path(ctx.session_key)

    from . import scripts as scripts_mod

    scripts_dir = str(paths.xdg_config_dir() / "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    ctx.scripts.manager = scripts_mod.ScriptManager(scripts_dir=scripts_dir, log=log)


# ED 2 (erase display) without an adjacent HOME -- inject HOME before it.
# Matches \033[2J that is NOT immediately preceded by \033[H or \033[1;1H.
ED2 = b"\x1b[2J"
HOME = b"\x1b[H"
HOME_ED2 = b"\x1b[H\x1b[2J"


def inject_home_before_clear(data: bytes) -> bytes:
    """
    Insert ``CSI H`` before any ``CSI 2 J`` not already preceded by ``CSI H``.

    BBS software often sends ``ED 2`` expecting it to also home the cursor (CTerm/SyncTERM behavior), but VT100-spec
    terminals and pyte do not. This injects the missing ``HOME`` so the real terminal behaves as expected.
    """
    if ED2 not in data:
        return data
    # Already has HOME before every ED 2 -- fast path
    if HOME_ED2 in data and data.count(ED2) == data.count(HOME_ED2):
        return data
    result = bytearray()
    i = 0
    while i < len(data):
        ed2_pos = data.find(ED2, i)
        if ed2_pos == -1:
            result.extend(data[i:])
            break
        result.extend(data[i:ed2_pos])
        # Check if HOME immediately precedes
        if len(result) >= 3 and result[-3:] == bytearray(HOME):
            result.extend(ED2)
        else:
            result.extend(HOME_ED2)
        i = ed2_pos + len(ED2)
    return bytes(result)


FF = b"\x0c"


def replace_ff_with_clear(data: bytes) -> bytes:
    """
    Replace Form Feed (``0x0C``) with ``CSI H CSI 2 J`` (home + erase display).

    SyncTERM and many BBS terminals treat FF as a clear-screen-and-home operation.  Standard VT100 terminals do not.
    This rewrites FF so the real terminal clears as the BBS expects.
    """
    if FF not in data:
        return data
    return data.replace(FF, HOME_ED2)


class ClearHomesWriter:
    """
    Wraps a stream writer to apply BBS clear-screen compatibility rewrites.

    Used in raw mode without a color filter when ``clear_homes_cursor`` or ``ff_clears_screen`` is enabled.

    :param inner: The underlying ``asyncio.StreamWriter``.
    :param clear_homes_cursor: Inject HOME before lone ED 2 sequences.
    :param ff_clears_screen: Replace Form Feed with HOME + ED 2.
    """

    def __init__(
        self, inner: asyncio.StreamWriter, clear_homes_cursor: bool = True, ff_clears_screen: bool = False
    ) -> None:
        self.inner = inner
        self.clear_homes_cursor = clear_homes_cursor
        self.ff_clears_screen = ff_clears_screen

    def write(self, data: bytes) -> None:
        if self.ff_clears_screen:
            data = replace_ff_with_clear(data)
        if self.clear_homes_cursor:
            data = inject_home_before_clear(data)
        self.inner.write(data)

    def __getattr__(self, name: str) -> typing.Any:
        return getattr(self.inner, name)


class ColorFilteredWriter:
    """
    Wraps an ``asyncio.StreamWriter`` to apply the session color filter to all writes.

    Used in raw-mode paths where :func:`telnetlib3.client_shell._raw_event_loop` writes decoded server text as bytes
    directly to stdout, bypassing the REPL's filter step.

    :param inner: The underlying ``asyncio.StreamWriter``.
    :param ctx: Session context carrying ``color_filter`` and ``erase_eol``.
    :param encoding: Character encoding for decoding bytes before filtering.
    """

    def __init__(
        self, inner: asyncio.StreamWriter, ctx: session_context.TelixSessionContext, encoding: "str | None" = None
    ) -> None:
        self.inner = inner
        self.ctx = ctx
        self.encoding = encoding or "utf-8"
        self._decoder: codecs.IncrementalDecoder | None = None

    def write(self, data: bytes) -> None:
        """
        Filter *data* through the color filter if one is active, then write.

        *data* arrives as UTF-8 encoded bytes from telnetlib3's ``_raw_event_loop``, which decodes wire bytes using the
        connection encoding (e.g. atascii) and re-encodes as UTF-8 with ``out.encode()``. We decode as UTF-8 to recover
        the original Unicode string, filter, and re-encode as UTF-8.
        """
        if self.ctx.repl.ff_clears_screen:
            data = replace_ff_with_clear(data)
        if self.ctx.repl.clear_homes_cursor:
            data = inject_home_before_clear(data)
        cf = self.ctx.repl.color_filter
        if cf is not None:
            if self._decoder is None:
                self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            text = self._decoder.decode(data)
            text = cf.filter(text)
            if self.ctx.repl.erase_eol:
                text = util.erase_eol(text)
            data = text.encode("utf-8", errors="replace")
        self.inner.write(data)

    def __getattr__(self, name: str) -> typing.Any:
        return getattr(self.inner, name)


def build_session_key(
    writer: (
        telnetlib3.stream_writer.TelnetWriter
        | telnetlib3.stream_writer.TelnetWriterUnicode
        | ws_transport.WebSocketWriter
        | ssh_transport.SSHWriter
    ),
) -> str:
    """
    Derive ``host:port`` session key from CLI arguments or peername.

    For telnet writers, prefers the original hostname from ``sys.argv`` over the resolved IP from
    :func:`socket.getpeername`, so that session-specific files (history, rooms, macros, etc.) are keyed by the human-
    readable hostname used to connect.

    For WebSocket and SSH writers, falls through directly to peername since the hostname is already set by the
    respective client.
    """
    if isinstance(writer, (ws_transport.WebSocketWriter, ssh_transport.SSHWriter)):
        peername = writer.get_extra_info("peername")
        if peername:
            return f"{peername[0]}:{peername[1]}"  # type: ignore[index]
        return ""
    _stderr_buf = io.StringIO()
    try:
        from . import main as _main_mod

        # Strip telix-specific args (e.g. --colormatch none) before passing to telnetlib3's parser
        # so they don't get misread as the positional port argument.
        stripped = _main_mod.build_telix_parser().parse_known_args(sys.argv[1:])[1]
        with contextlib.redirect_stderr(_stderr_buf):
            args = telnetlib3.client._get_argument_parser().parse_known_args(stripped)[0]
        if args.host and not args.host.startswith(("ws://", "wss://")):
            return f"{args.host}:{args.port}"
    except SystemExit:
        pass
    finally:
        lines = [line for line in _stderr_buf.getvalue().splitlines() if line.strip()]
        if lines:
            log.error("argv parse error (command: %s):", shlex.join(sys.argv))
            for line in lines:
                log.error("%s", line)
            # "required" errors mean argv lacks connection info; use peername fallback.
            # "invalid" errors (wrong type, bad value) indicate a real misconfiguration.
            if any("invalid" in line.lower() for line in lines):
                sys.exit(1)
    peername = writer.get_extra_info("peername")
    if peername:
        return f"{peername[0]}:{peername[1]}"
    return ""


def want_repl(
    ctx: session_context.TelixSessionContext,
    writer: (telnetlib3.stream_writer.TelnetWriter | telnetlib3.stream_writer.TelnetWriterUnicode),
) -> bool:
    """Return True when the REPL should be active."""
    if ctx.raw_mode is True:
        return False
    return ctx.repl.enabled and getattr(writer, "mode", "local") == "local"


def setup_color_filter(
    ctx: session_context.TelixSessionContext,
    writer: (
        telnetlib3.stream_writer.TelnetWriter
        | telnetlib3.stream_writer.TelnetWriterUnicode
        | ws_transport.WebSocketWriter
    ),
) -> None:
    """
    Create and attach a color filter from telix CLI args and terminal detection.

    Reads color options from ``ctx.color_args`` (threaded through the call chain from :func:`~telix.main.main`) and
    encoding from the telnetlib3 writer context. For retro encodings (PETSCII, ATASCII), uses the encoding-specific
    filter instead of ColorFilter.
    """
    from . import color_filter

    args = ctx.color_args
    if args is None:
        return

    colormatch: str = args.colormatch or "vga"
    if colormatch.lower() == "none":
        return

    encoding_name: str = getattr(writer.ctx, "encoding", "") or ""
    if not encoding_name:
        encoding_name = getattr(writer, "default_encoding", "") or ""
    is_petscii = encoding_name.lower() in ("petscii", "cbm", "commodore", "c64", "c128")
    is_atascii = encoding_name.lower() in ("atascii", "atari8bit", "atari_8bit")
    if colormatch == "petscii":
        colormatch = "c64"
    if is_petscii and colormatch != "c64":
        colormatch = "c64"

    if colormatch not in color_filter.PALETTES:
        log.warning("Unknown palette %r, disabling color filter", colormatch)
        return

    if is_petscii or colormatch == "c64":
        ctx.repl.color_filter = color_filter.PetsciiColorFilter(
            brightness=args.color_brightness, contrast=args.color_contrast
        )
        return

    if is_atascii:
        ctx.repl.color_filter = color_filter.AtasciiControlFilter()
        return

    bg_color: tuple[int, int, int] = (0, 0, 0)
    bg_str = args.background_color
    if isinstance(bg_str, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", bg_str):
        bg_color = (int(bg_str[1:3], 16), int(bg_str[3:5], 16), int(bg_str[5:7], 16))
    elif isinstance(bg_str, tuple):
        bg_color = bg_str
    fg_color: tuple[int, int, int] | None = None

    # Terminal colors detected at startup (before any framework took stdin) are
    # stored in env vars so subprocess connections inherit them automatically.
    bg_env = os.environ.get("TELIX_DETECTED_BG")
    if bg_env:
        r, g, b = (int(x) for x in bg_env.split(","))
        bg_color = (r, g, b)
    fg_env = os.environ.get("TELIX_DETECTED_FG")
    if fg_env:
        r, g, b = (int(x) for x in fg_env.split(","))
        fg_color = (r, g, b)

    color_config = color_filter.ColorConfig(
        palette_name=colormatch,
        brightness=args.color_brightness,
        contrast=args.color_contrast,
        background_color=bg_color,
        ice_colors=not args.no_ice_colors,
        foreground_color=fg_color,
    )
    ctx.repl.color_filter = color_filter.ColorFilter(color_config)
    ctx.repl.erase_eol = True


def setup_ansi_keys(ctx: "session_context.TelixSessionContext") -> None:
    """
    Set ``ctx.ansi_keys`` from the telix CLI ``--ansi-keys`` flag.

    Reads ``ctx.color_args`` threaded through the call chain from :func:`~telix.main.main`.  No-op when called outside a
    main() context.

    :param ctx: Session context to update.
    """
    args = ctx.color_args
    if args is None:
        return
    ctx.repl.ansi_keys = args.ansi_keys


def setup_clear_homes(ctx: "session_context.TelixSessionContext") -> None:
    """
    Configure the clear-homes-cursor option from telix CLI args.

    :param ctx: Session context to update.
    """
    args = ctx.color_args
    if args is None:
        return
    ctx.repl.clear_homes_cursor = args.clear_homes_cursor
    ctx.repl.ff_clears_screen = args.ff_clears_screen


def setup_graphics_font(ctx: "session_context.TelixSessionContext") -> None:
    """
    Configure graphics font rendering from telix CLI args.

    Reads ``ctx.color_args`` for ``--graphics-font``, ``--graphics-columns``, and ``--graphics-rows`` flags.

    :param ctx: Session context to update.
    """
    args = ctx.color_args
    if args is None:
        return
    ctx.repl.graphics_font = args.graphics_font
    ctx.repl.graphics_columns = args.graphics_columns
    ctx.repl.graphics_rows = args.graphics_rows


def setup_font_id(ctx: "session_context.TelixSessionContext") -> None:
    """
    Configure initial font id for graphics rendering from CLI args.

    When ``--font-id`` is specified, overrides the default font (0, IBM VGA).  Otherwise, if the session encoding is a
    retro encoding (ATASCII, PETSCII), selects the matching font automatically.

    :param ctx: Session context to update.
    """
    args = ctx.color_args
    if args is not None and args.font_id is not None:
        ctx.repl.font_id = args.font_id
        return

    enc = ctx.encoding or ""
    if enc.lower() == "atascii":
        ctx.repl.font_id = 36
    elif enc.lower() in ("petscii", "cbm", "commodore", "c64", "c128"):
        ctx.repl.font_id = 32


def _setup_resize_and_naws(raw_stdout: typing.Any, writer: typing.Any, tty_shell: typing.Any) -> None:
    """Wire SIGWINCH handler and NAWS reporting for a meta/graphics writer."""
    if writer is not None and hasattr(writer, "handle_send_naws"):

        def _naws() -> tuple[int, int]:
            return raw_stdout.virtual_size()

        writer.handle_send_naws = _naws  # type: ignore[method-assign]
    if tty_shell is not None and hasattr(tty_shell, "_resize_pending"):
        import signal

        import telnetlib3.telopt

        try:
            loop = asyncio.get_event_loop()

            def _winch() -> None:
                real_rows, real_cols = terminal.get_terminal_size()
                raw_stdout.resize(real_cols, real_rows)
                tty_shell._resize_pending.set()
                if hasattr(writer, "local_option") and writer.local_option.enabled(telnetlib3.telopt.NAWS):
                    writer._send_naws()

            loop.add_signal_handler(signal.SIGWINCH, _winch)
        except (RuntimeError, ValueError):
            pass


def make_raw_stdout(
    stdout: asyncio.StreamWriter,
    ctx: "session_context.TelixSessionContext",
    tty_shell: "typing.Any | None" = None,
    writer: "typing.Any | None" = None,
) -> "asyncio.StreamWriter | ColorFilteredWriter":
    """
    Build the raw-mode stdout wrapper appropriate for the session config.

    Returns a :class:`GraphicsWriter` when ``graphics_font`` is ``"auto"``,
    a :class:`ColorFilteredWriter` when a color filter is active,
    or the raw *stdout* otherwise.

    When a graphics font is enabled, also patches NAWS reporting on *writer*
    to return the virtual terminal size, and sets *tty_shell.on_resize* to
    schedule a resize on the next write (no I/O from the signal handler).

    :param stdout: The underlying asyncio stream to real stdout.
    :param ctx: Session context with display configuration.
    :param tty_shell: Terminal shell object (for resize callback).
    :param writer: Telnet/WS/SSH writer (for NAWS patching).
    :returns: Wrapped or raw stdout writer.
    """
    if ctx.repl.graphics_font == "auto":
        import blessed

        from . import graphics_writer, graphics_renderer

        term = blessed.Terminal()
        protocol = graphics_renderer.detect_graphics_protocol(term)
        if protocol is None:
            log.warning("graphics_font enabled but no graphics protocol detected; falling back")
        else:
            real_rows, real_cols = terminal.get_terminal_size()
            px_h, px_w = term.get_sixel_height_and_width(timeout=0.5)
            if px_h == -1:
                px_h, px_w = 0, 0
            cell_px_w = px_w // real_cols if px_w and real_cols else 0
            cell_px_h = px_h // real_rows if px_h and real_rows else 0
            log.debug("cell px: %dx%d (from XTWINOPS 14t/16t)", cell_px_w, cell_px_h)
            columns = ctx.repl.graphics_columns or 80
            rows = ctx.repl.graphics_rows or 25
            gtw = graphics_writer.GraphicsWriter(
                stdout,
                ctx,
                protocol,
                columns=columns,
                rows=rows,
                cell_px_w=cell_px_w,
                cell_px_h=cell_px_h,
                font_id=ctx.repl.font_id,
            )
            _setup_resize_and_naws(gtw, writer, tty_shell)
            return gtw

    if ctx.repl.color_filter is not None:
        return ColorFilteredWriter(stdout, ctx)
    if ctx.repl.clear_homes_cursor or ctx.repl.ff_clears_screen:
        return ClearHomesWriter(
            stdout, clear_homes_cursor=ctx.repl.clear_homes_cursor, ff_clears_screen=ctx.repl.ff_clears_screen
        )
    return stdout


async def telix_client_shell(
    telnet_reader: (telnetlib3.stream_reader.TelnetReader | telnetlib3.stream_reader.TelnetReaderUnicode),
    telnet_writer: (telnetlib3.stream_writer.TelnetWriter | telnetlib3.stream_writer.TelnetWriterUnicode),
) -> None:
    """
    Telix client shell with REPL/raw mode switching.

    Drop-in replacement for
    :func:`telnetlib3.client_shell.telnet_client_shell`.
    Creates a :class:`SessionContext`, loads configs, and runs an outer
    loop that alternates between the REPL (line-mode) and raw event loop
    based on telnet negotiation state.

    :param telnet_reader: Server-side telnet reader stream.
    :param telnet_writer: Client-side telnet writer stream.
    """
    # 1. Build SessionContext and attach to writer, preserving attributes
    #    that run_client() wrappers already set on the original ctx.
    # Transfer color_args from module-level global to initial ctx for native telnet path.
    from . import main as _main_mod

    telnet_writer.ctx.color_args = _main_mod._color_args  # type: ignore[attr-defined]
    ctx = telnet_writer.ctx = session_context.TelixSessionContext.create_using_telnet_ctx(
        writer=telnet_writer,  # type: ignore[arg-type]
        session_key=build_session_key(telnet_writer),
        encoding=telnet_writer.fn_encoding(incoming=True),
    )
    ctx.repl.enabled = not _main_mod._color_args.no_repl
    if hasattr(_main_mod._color_args, "echo_mode"):
        ctx.echo_mode = _main_mod._color_args.echo_mode

    # 2. Load per-session configs, set up color filter from CLI arguments
    load_configs(ctx)

    setup_color_filter(ctx, telnet_writer)
    setup_ansi_keys(ctx)
    setup_clear_homes(ctx)
    setup_graphics_font(ctx)
    setup_font_id(ctx)

    # 3. Setup GMCP callbacks
    base_on_gmcp = telnet_writer._ext_callback.get(telnetlib3.telopt.GMCP)

    def on_gmcp(package: str, data: typing.Any) -> None:
        package = ".".join(seg.title() for seg in package.split("."))
        if base_on_gmcp is not None:
            base_on_gmcp(package, data)
        if package == "Comm.Channel.Text":
            if ctx.chat.on_text is not None:
                ctx.chat.on_text(data)
        elif package == "Comm.Channel.List":
            if ctx.chat.on_channels is not None:
                ctx.chat.on_channels(data)
        elif package == "Room.Info":
            if ctx.gmcp.on_room_info is not None:
                ctx.gmcp.on_room_info(data)
        evt = ctx.gmcp.package_events.get(package)
        if evt is not None:
            evt.set()
            evt.clear()
        ctx.gmcp.any_update.set()
        ctx.gmcp.any_update.clear()

    telnet_writer.set_ext_callback(telnetlib3.telopt.GMCP, on_gmcp)

    keyboard_escape = ctx.repl.keyboard_escape

    with telnetlib3.client_shell.Terminal(telnet_writer=telnet_writer) as tty_shell:
        linesep = "\n"
        switched_to_raw = False
        last_will_echo = False
        local_echo = tty_shell.software_echo
        if tty_shell._istty:
            raw_mode = telnetlib3.client_shell._get_raw_mode(telnet_writer)
            if telnet_writer.will_echo or raw_mode is True:
                linesep = "\r\n"
        stdout = await tty_shell.make_stdout()  # pylint: disable=no-member
        tty_shell.setup_winch()

        # EOR/GA-based command pacing for raw-mode triggers.
        prompt_ready_raw = asyncio.Event()
        prompt_ready_raw.set()
        ga_detected_raw = False

        def on_prompt_signal_raw(cmd: bytes) -> None:
            nonlocal ga_detected_raw
            ga_detected_raw = True
            prompt_ready_raw.set()
            ar = ctx.triggers.engine
            if ar is not None:
                ar.on_prompt()

        telnet_writer.set_iac_callback(telnetlib3.telopt.GA, on_prompt_signal_raw)
        telnet_writer.set_iac_callback(telnetlib3.telopt.CMD_EOR, on_prompt_signal_raw)

        async def wait_for_prompt_raw() -> None:
            if not ga_detected_raw:
                return
            try:
                await asyncio.wait_for(prompt_ready_raw.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            prompt_ready_raw.clear()

        ctx.triggers.wait_fn = wait_for_prompt_raw
        ctx.autoreply_wait_fn = wait_for_prompt_raw

        escape_name = telnetlib3.accessories.name_unicode(keyboard_escape)
        banner_sep = "\r\n" if tty_shell._istty else linesep
        stdout.write(f"Escape character is '{escape_name}'.{banner_sep}".encode())

        raw_stdout_ref: list[typing.Any] = [None]

        def handle_close(msg: str) -> None:
            cf = ctx.repl.color_filter
            if cf is not None:
                flush = cf.flush()
                if flush:
                    stdout.write(flush.encode())
            rs = raw_stdout_ref[0]
            if rs is not None and hasattr(rs, "cleanup"):
                rs.cleanup()
            stdout.write(f"\033[m{linesep}{msg}{linesep}".encode())
            tty_shell.cleanup_winch()

        def check_want_repl() -> bool:
            return want_repl(ctx, telnet_writer)

        # Wait briefly for negotiation to settle before deciding to
        # enter the REPL or evaluating will_echo for local_echo.
        # Servers that negotiate ECHO+SGA (kludge mode) often send
        # those options shortly after connection, and entering the
        # REPL only to immediately exit causes scroll region
        # corruption.  In forced raw mode, skipping this wait causes
        # local_echo to be set before the server negotiates WILL ECHO,
        # resulting in double echo (local + server).
        if ctx.raw_mode is not False and tty_shell._istty:
            try:
                await asyncio.wait_for(telnet_writer.wait_for_condition(lambda w: w.mode != "local"), timeout=0.05)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # Outer loop: alternate between REPL and raw modes.
        while True:
            if check_want_repl() and tty_shell._istty:
                mode_switched = await client_repl.repl_event_loop(
                    telnet_reader, telnet_writer, tty_shell, stdout, history_file=ctx.repl.history_file
                )
                if not mode_switched:
                    # Connection closed normally.
                    break
                # Server switched to kludge/raw mode -- fall through.

            # Raw event loop.
            if not switched_to_raw and tty_shell._istty and tty_shell._save_mode is not None:
                tty_shell.set_mode(tty_shell._make_raw(tty_shell._save_mode, suppress_echo=True))
                switched_to_raw = True
                local_echo = compute_local_echo(ctx.echo_mode, telnet_writer.will_echo)
                linesep = "\r\n"
            stdin = await tty_shell.connect_stdin()  # pylint: disable=no-member
            if not ctx.repl.ansi_keys:
                _apply_delete_to_backspace(stdin)
            state = telnetlib3.client_shell._RawLoopState(
                switched_to_raw=switched_to_raw, last_will_echo=last_will_echo, local_echo=local_echo, linesep=linesep
            )
            raw_stdout = make_raw_stdout(stdout, ctx, tty_shell=tty_shell, writer=telnet_writer)
            raw_stdout_ref[0] = raw_stdout
            try:
                await telnetlib3.client_shell._raw_event_loop(
                    telnet_reader,
                    telnet_writer,
                    tty_shell,
                    stdin,
                    raw_stdout,
                    keyboard_escape,
                    state,
                    handle_close,
                    check_want_repl,
                )
            except Exception:
                log.exception("Unhandled exception in raw event loop")
                raw_stdout._output("\033[?25h\033[?1049l")
                raw_stdout._output("\r\n\r\n[press Enter to return to session manager]\r\n")
                try:
                    await asyncio.get_event_loop().run_in_executor(None, input)
                except Exception:
                    pass
                break
            tty_shell.disconnect_stdin(stdin)  # pylint: disable=no-member
            # Carry forward state from the raw loop.
            switched_to_raw = state.switched_to_raw
            last_will_echo = state.last_will_echo
            local_echo = state.local_echo
            linesep = state.linesep
            if state.reactivate_repl and check_want_repl():
                # Server returned to line mode -- loop back to REPL.
                continue
            # Connection closed.
            break

        ctx.close()


async def ssh_client_shell(ssh_reader: ssh_transport.SSHReader, ssh_writer: ssh_transport.SSHWriter) -> None:
    """
    Telix client shell for SSH connections.

    SSH connections are always BBS/raw mode -- no telnet negotiation occurs, so
    there is no REPL line-mode switching.  Creates a
    :class:`~telix.session_context.TelixSessionContext`, loads configs, and
    runs a single raw event-loop pass.

    :param ssh_reader: :class:`~telix.ssh_transport.SSHReader` fed by the receive loop.
    :param ssh_writer: :class:`~telix.ssh_transport.SSHWriter` wrapping the SSH process.
    """
    import telnetlib3._session_context

    ssh_writer.ctx = telnetlib3._session_context.TelnetSessionContext()  # type: ignore[assignment]
    ssh_writer.ctx.color_args = getattr(ssh_writer, "color_args", None)

    ctx = ssh_writer.ctx = session_context.TelixSessionContext.create_using_telnet_ctx(
        session_key=build_session_key(ssh_writer),
        writer=ssh_writer,  # type: ignore[arg-type]
        encoding=ssh_writer.encoding,
    )
    ctx.repl.enabled = False
    ctx.raw_mode = True

    load_configs(ctx)
    setup_color_filter(ctx, ssh_writer)  # type: ignore[arg-type]
    setup_clear_homes(ctx)
    setup_graphics_font(ctx)
    setup_font_id(ctx)

    keyboard_escape = ctx.repl.keyboard_escape

    with telnetlib3.client_shell.Terminal(telnet_writer=ssh_writer) as tty_shell:  # type: ignore[arg-type]
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
            if rs is not None and hasattr(rs, "cleanup"):
                rs.cleanup()
            stdout.write(f"\033[m{linesep}{msg}{linesep}".encode())
            tty_shell.cleanup_winch()

        if tty_shell._istty:
            if tty_shell._save_mode is not None:
                tty_shell.set_mode(tty_shell._make_raw(tty_shell._save_mode, suppress_echo=True))
            stdin = await tty_shell.connect_stdin()  # pylint: disable=no-member
            if not ctx.repl.ansi_keys:
                _apply_delete_to_backspace(stdin)
            state = telnetlib3.client_shell._RawLoopState(
                switched_to_raw=True, last_will_echo=False, local_echo=False, linesep=linesep
            )
            raw_stdout = make_raw_stdout(stdout, ctx, tty_shell=tty_shell, writer=ssh_writer)
            raw_stdout_ref[0] = raw_stdout

            ts_path = getattr(ssh_writer, "typescript", "")
            if ts_path:
                ts_file = open(ts_path, "wb")
                _inner_write = raw_stdout.write

                def _tee_write(data: bytes) -> None:
                    _inner_write(data)
                    ts_file.write(data)
                    ts_file.flush()

                raw_stdout.write = _tee_write  # type: ignore[method-assign]
                _orig_cleanup = getattr(raw_stdout, "cleanup", None)

                def _tee_cleanup() -> None:
                    if _orig_cleanup is not None:
                        _orig_cleanup()
                    ts_file.close()

                raw_stdout.cleanup = _tee_cleanup  # type: ignore[attr-defined]

            try:
                await telnetlib3.client_shell._raw_event_loop(
                    ssh_reader,  # type: ignore[arg-type]
                    ssh_writer,  # type: ignore[arg-type]
                    tty_shell,
                    stdin,
                    raw_stdout,
                    keyboard_escape,
                    state,
                    handle_close,
                    lambda: False,
                )
            except Exception:
                log.exception("Unhandled exception in SSH raw event loop")
                handle_close("Connection closed.")
            tty_shell.disconnect_stdin(stdin)  # pylint: disable=no-member
        else:
            handle_close("Connection closed.")
    ctx.close()


async def ws_client_shell(ws_reader: ws_transport.WebSocketReader, ws_writer: ws_transport.WebSocketWriter) -> None:
    """
    Telix client shell for WebSocket connections.

    Simpler counterpart to :func:`telix_client_shell` -- WebSocket connections are always line-mode (no raw/kludge
    switching), so this function creates a :class:`SessionContext`, loads configs, wires GMCP dispatch, and runs a
    single pass of the REPL event loop.

    The pseudo-prompt signal (GA/EOR) is fired by the receive loop in :mod:`~telix.ws_client` after each BINARY frame
    delivery, giving the REPL the same prompt boundary as telnet.

    :param reader: :class:`WebSocketReader` fed by the receive loop.
    :param writer: :class:`WebSocketWriter` wrapping the WebSocket connection.
    """
    # 1. Build SessionContext and attach to writer, preserving attributes from initial ctx.
    no_repl = getattr(ws_writer.ctx, "no_repl", False)
    ctx = ws_writer.ctx = session_context.TelixSessionContext.create_using_telnet_ctx(
        session_key=build_session_key(ws_writer), writer=ws_writer, encoding=ws_writer.encoding
    )
    ctx.repl.enabled = not no_repl
    if color_args := getattr(ws_writer.ctx, "color_args", None):
        if hasattr(color_args, "echo_mode"):
            ctx.echo_mode = color_args.echo_mode

    # 2. Load per-session configs.
    load_configs(ctx)

    # 2b. Set up color filter and graphics font from CLI args.
    setup_color_filter(ctx, ws_writer)
    setup_clear_homes(ctx)
    setup_graphics_font(ctx)
    setup_font_id(ctx)

    # 3. Wire GMCP dispatch (no base callback -- WebSocket has none).
    def on_gmcp(package: str, data: typing.Any) -> None:
        package = ".".join(seg.title() for seg in package.split("."))
        if package == "Comm.Channel.Text":
            if ctx.chat.on_text is not None:
                ctx.chat.on_text(data)
        elif package == "Comm.Channel.List":
            if ctx.chat.on_channels is not None:
                ctx.chat.on_channels(data)
        elif package == "Room.Info":
            if ctx.gmcp.on_room_info is not None:
                ctx.gmcp.on_room_info(data)
        evt = ctx.gmcp.package_events.get(package)
        if evt is not None:
            evt.set()
            evt.clear()
        ctx.gmcp.any_update.set()
        ctx.gmcp.any_update.clear()

    ws_writer.set_ext_callback(ws_transport.GMCP, on_gmcp)

    keyboard_escape = ctx.repl.keyboard_escape

    # Terminal / repl_event_loop / _flush_color_filter are typed for
    # TelnetWriter but accept any duck-compatible writer at runtime.
    with telnetlib3.client_shell.Terminal(telnet_writer=ws_writer) as tty_shell:  # type: ignore[arg-type]
        linesep = "\n"
        stdout = await tty_shell.make_stdout()
        tty_shell.setup_winch()

        escape_name = telnetlib3.accessories.name_unicode(keyboard_escape)
        banner_sep = "\r\n" if tty_shell._istty else linesep
        stdout.write(f"Escape character is '{escape_name}'.{banner_sep}".encode())

        raw_stdout_ref: list[typing.Any] = [None]

        def handle_close(msg: str) -> None:
            cf = ctx.repl.color_filter
            if cf is not None:
                flush = cf.flush()
                if flush:
                    stdout.write(flush.encode())
            rs = raw_stdout_ref[0]
            if rs is not None and hasattr(rs, "cleanup"):
                rs.cleanup()
            stdout.write(f"\033[m{linesep}{msg}{linesep}".encode())
            tty_shell.cleanup_winch()

        if tty_shell._istty and ctx.repl.enabled:
            await client_repl.repl_event_loop(
                ws_reader,  # type: ignore[arg-type]
                ws_writer,  # type: ignore[arg-type]
                tty_shell,
                stdout,
                history_file=ctx.repl.history_file,
            )
            handle_close("Connection closed.")
        elif tty_shell._istty:
            # Raw mode: byte-at-a-time I/O for BBS connections.
            if tty_shell._save_mode is not None:
                tty_shell.set_mode(tty_shell._make_raw(tty_shell._save_mode, suppress_echo=True))
            linesep = "\r\n"
            stdin = await tty_shell.connect_stdin()  # pylint: disable=no-member
            if not ctx.repl.ansi_keys:
                _apply_delete_to_backspace(stdin)
            state = telnetlib3.client_shell._RawLoopState(
                switched_to_raw=True,
                last_will_echo=False,
                local_echo=compute_local_echo(ctx.echo_mode, ws_writer.will_echo),
                linesep=linesep,
            )
            raw_stdout = make_raw_stdout(stdout, ctx, tty_shell=tty_shell, writer=ws_writer)
            raw_stdout_ref[0] = raw_stdout
            await telnetlib3.client_shell._raw_event_loop(
                ws_reader,  # type: ignore[arg-type]
                ws_writer,  # type: ignore[arg-type]
                tty_shell,
                stdin,
                raw_stdout,
                keyboard_escape,
                state,
                handle_close,
                lambda: False,  # never switch back to REPL
            )
            tty_shell.disconnect_stdin(stdin)  # pylint: disable=no-member
        else:
            handle_close("Connection closed.")
    ctx.close()
