"""
Raw TCP reader/writer adapters for telix sessions.

Provides :class:`RawReader` and :class:`RawWriter`, which present a compatible interface to telnetlib3's reader/writer
so that the REPL can operate over a raw TCP transport without modification.

:class:`RawReader` is a queue-based reader fed by the raw socket receive loop. :class:`RawWriter` wraps an
:class:`asyncio.StreamWriter` for writing and carries the same telnet compatibility stubs as
:class:`~telix.ssh_transport.SSHWriter`.
"""

import typing
import asyncio
import logging
from collections.abc import Callable

if typing.TYPE_CHECKING:
    from telix.session_context import TelixSessionContext

log = logging.getLogger(__name__)

__all__ = ("RawReader", "RawWriter")


class RawReader:
    """
    Async reader fed by a raw TCP receive loop.

    Presents the same ``read()`` / ``at_eof()`` interface as :class:`~telnetlib3.stream_reader.TelnetReader` so the
    REPL's ``_read_server`` loop works without changes.

    ``feed_data`` accepts a ``str`` because the receive loop decodes raw bytes before feeding.
    """

    def __init__(self) -> None:
        """Initialise the reader with an empty queue."""
        self._buffer: asyncio.Queue[str | None] = asyncio.Queue()
        self._eof = False

    def feed_data(self, data: str) -> None:
        """
        Enqueue text received from the raw TCP socket.

        :param data: Decoded text from the server.
        """
        self._buffer.put_nowait(data)

    def feed_eof(self) -> None:
        """Signal end-of-stream."""
        self._eof = True
        self._buffer.put_nowait(None)

    def at_eof(self) -> bool:
        """Return ``True`` if EOF has been signalled."""
        return self._eof

    async def read(self, n: int = -1) -> str:
        """
        Read the next chunk of server text.

        Blocks until data is available.  Returns an empty string at EOF.

        :param n: Ignored (present for API compatibility).
        """
        if self._eof and self._buffer.empty():
            return ""
        chunk = await self._buffer.get()
        if chunk is None:
            return ""
        return chunk

    def _wakeup_waiter(self) -> None:
        """Wake any blocked ``read()`` call (feed empty string to unblock)."""
        self._buffer.put_nowait("")


class NullOptionSet:
    """Stub for ``telnet_writer.local_option`` / ``remote_option``."""

    @staticmethod
    def enabled(key: object) -> bool:
        """Return ``False`` for all telnet options."""
        return False


class RawWriter:
    """
    Writer that sends data over a raw TCP socket.

    Presents the subset of :class:`~telnetlib3.stream_writer.TelnetWriter`
    that the REPL and shell actually use: ``write()``, ``close()``,
    ``is_closing()``, ``will_echo``, ``mode``, ``get_extra_info()``,
    ``set_ext_callback()``, ``set_iac_callback()``, and ``log``.

    Also provides stubs for telnet-specific attributes (``local_option``,
    ``remote_option``, ``client``, ``_send_naws``, ``handle_send_naws``)
    so that code shared with the telnet path does not need conditionals.
    """

    def __init__(
        self, stream_writer: asyncio.StreamWriter | None = None, peername: tuple[str, int] | None = None
    ) -> None:
        """
        Initialise the writer.

        :param stream_writer: The asyncio StreamWriter; may be None initially and set later once connected.
        :param peername: (host, port) tuple for get_extra_info("peername").
        """
        self._stream_writer: asyncio.StreamWriter | None = stream_writer
        self._peername = peername
        self._closing = False
        self._ext_callback: dict[bytes, Callable[..., object]] = {}
        self._iac_callback: dict[bytes, Callable[..., object]] = {}
        self.log = logging.getLogger("telix.raw_transport")
        self.encoding: str = "utf-8"
        self.encoding_errors: str = "replace"
        self.ctx: TelixSessionContext = None  # type: ignore[assignment]
        self.will_echo: bool = False
        self.mode: str = "local"

        # Telnetlib3 compatibility stubs.
        self.local_option = NullOptionSet()
        self.remote_option = NullOptionSet()
        self.client: bool = True
        self.handle_send_naws: Callable[[], None] | None = None

    @property
    def stream_writer(self) -> "asyncio.StreamWriter | None":
        """Return the underlying asyncio StreamWriter, or ``None`` before connect."""
        return self._stream_writer

    @stream_writer.setter
    def stream_writer(self, value: "asyncio.StreamWriter | None") -> None:
        self._stream_writer = value

    def write(self, text: str | bytes) -> None:
        """
        Write *text* to the raw TCP socket.

        :param text: Text to send; bytes are decoded to str before encoding with the connection encoding.
        """
        if self._stream_writer is None:
            return
        if isinstance(text, bytes):
            text = text.decode(self.encoding, errors=self.encoding_errors)
        data = text.encode(self.encoding, errors=self.encoding_errors)
        self._stream_writer.write(data)

    def _write(self, buf: bytes, escape_iac: bool = True) -> None:
        """
        Write raw bytes (telnetlib3 raw event-loop compatibility).

        :param buf: Bytes to send.
        :param escape_iac: Ignored (raw TCP has no IAC interpretation).
        """
        if self._stream_writer is None:
            return
        self._stream_writer.write(buf)

    def _send_naws(self) -> None:
        """
        Report current terminal size.

        Uses ``handle_send_naws`` when patched by the graphics writer to report the virtual terminal size instead of the
        real one.
        """
        if self.handle_send_naws is not None:
            self.handle_send_naws()

    def close(self) -> None:
        """Close the raw TCP socket."""
        if not self._closing:
            self._closing = True
            if self._stream_writer is not None:
                self._stream_writer.close()

    def is_closing(self) -> bool:
        """Return ``True`` if :meth:`close` has been called."""
        return self._closing

    def get_extra_info(self, name: str, default: object = None) -> object:
        """
        Return transport metadata.

        :param name: Key name (only "peername" supported).
        :param default: Value to return if *name* is not available.
        """
        if name == "peername":
            return self._peername if self._peername is not None else default
        return default

    def set_ext_callback(self, key: bytes, callback: Callable[..., object]) -> None:
        r"""
        Register an extension callback (no-op for raw TCP).

        :param key: Telopt byte.
        :param callback: Callable receiving (package, data).
        """
        self._ext_callback[key] = callback

    def set_iac_callback(self, key: bytes, callback: Callable[..., object]) -> None:
        r"""
        Register an IAC callback (no-op for raw TCP).

        :param key: IAC command byte.
        :param callback: Callable receiving the command byte.
        """
        self._iac_callback[key] = callback
