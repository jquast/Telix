"""
WebSocket client entry point for telix.

Provides :func:`main`, the ``telix-ws`` console script entry point, and
:func:`run_ws_client`, which connects to a WebSocket MUD server using
the ``gmcp.mudstandards.org`` subprotocol, creates reader/writer
adapters, runs the receive loop, and invokes the telix shell.

This module mirrors the role of ``telnetlib3.client`` but for WebSocket
connections.  The TUI launches it as a subprocess (the same way it
launches ``telnetlib3-client`` for telnet sessions).
"""

from __future__ import annotations

# std imports
import sys
import asyncio
import logging
import argparse
from typing import Optional
from urllib.parse import urlparse

# local
from .ws_transport import WebSocketReader, WebSocketWriter, parse_gmcp_frame

log = logging.getLogger(__name__)

_WS_SUBPROTOCOL = "gmcp.mudstandards.org"


async def run_ws_client(url: str, shell: str = "telix.client_shell.ws_client_shell") -> None:
    """
    Connect to a WebSocket MUD server and run the telix shell.

    :param url: WebSocket URL (e.g. ``wss://gel.monster:8443``).
    :param shell: Dotted path to the shell coroutine.
    """
    import websockets
    import websockets.exceptions
    from websockets.typing import Subprotocol

    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)

    reader = WebSocketReader()
    writer: Optional[WebSocketWriter] = None

    # Resolve the shell function.
    from telnetlib3.accessories import function_lookup

    shell_fn = function_lookup(shell)

    async with websockets.connect(
        url, subprotocols=[Subprotocol(_WS_SUBPROTOCOL)], max_size=2**20, open_timeout=10
    ) as ws:
        writer = WebSocketWriter(ws, peername=(host, port))

        # Create a minimal session context for the writer.
        from telnetlib3._session_context import TelnetSessionContext

        writer.ctx = TelnetSessionContext()

        async def _receive_loop() -> None:
            """Read WebSocket frames and dispatch to reader/writer."""
            assert writer is not None
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        # BINARY frame = raw game text.
                        reader.feed_data(message)
                        writer.fire_prompt_signal()
                    elif isinstance(message, str):
                        # TEXT frame = GMCP message.
                        try:
                            pkg, data = parse_gmcp_frame(message)
                            writer.dispatch_gmcp(pkg, data)
                        except ValueError:
                            log.warning("invalid GMCP frame: %r", message[:80])
            except websockets.exceptions.ConnectionClosed:
                log.debug("WebSocket connection closed")
            finally:
                reader.feed_eof()

        recv_task = asyncio.ensure_future(_receive_loop())
        drain_task = asyncio.ensure_future(writer.drain())

        try:
            await shell_fn(reader, writer)
        finally:
            writer.close()
            recv_task.cancel()
            for task in (recv_task, drain_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for ``telix-ws``."""
    parser = argparse.ArgumentParser(
        prog="telix-ws", description="Connect to a WebSocket MUD server."
    )
    parser.add_argument("url", help="WebSocket URL (e.g. wss://gel.monster:8443)")
    parser.add_argument(
        "--shell",
        default="telix.client_shell.ws_client_shell",
        help="Dotted path to shell coroutine (default: telix WS shell).",
    )
    return parser


def main() -> None:
    """Entry point for the ``telix-ws`` console script."""
    parser = _build_parser()
    args = parser.parse_args()

    try:
        asyncio.run(run_ws_client(url=args.url, shell=args.shell))
    except KeyboardInterrupt:
        pass
    except OSError as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
