"""Tests for telix.ssh_client -- key file path resolution, parser, and data delivery."""

import asyncio
import os

import pytest

from telix import ssh_client as ssh_client_mod
from telix.ssh_client import resolve_key_file, build_parser, SSHTelix
from telix.ssh_transport import SSHReader, SSHWriter


@pytest.mark.parametrize(
    "key_file, expected",
    [
        ("id_ed25519", "id_ed25519"),
        ("~/.ssh/id_ed25519", os.path.expanduser("~/.ssh/id_ed25519")),
        ("/absolute/path/key", "/absolute/path/key"),
        ("subdir/key", "subdir/key"),
        ("", ""),
    ],
)
def test_resolve_key_file(key_file, expected):
    """resolve_key_file expands ~ paths, leaving bare names as-is."""
    assert resolve_key_file(key_file) == expected


class TestBuildParser:
    def test_returns_parser(self):
        parser = build_parser()
        assert parser.prog == "telix-ssh"

    def test_parses_host(self):
        args = build_parser().parse_args(["example.com"])
        assert args.host == "example.com"
        assert args.port == 22

    def test_parses_all_options(self):
        args = build_parser().parse_args([
            "host", "--port", "2222", "--username", "user",
            "--key-file", "/key", "--term", "vt100",
            "--colormatch", "cga", "--color-brightness", "1.5",
        ])
        assert args.port == 2222
        assert args.username == "user"
        assert args.key_file == "/key"
        assert args.term == "vt100"
        assert args.colormatch == "cga"
        assert args.color_brightness == 1.5


class TestSSHTelixCallbacks:
    def test_kbdint_auth_requested_returns_empty(self):
        reader = SSHReader()
        writer = SSHWriter(peername=("host", 22))
        client = SSHTelix(reader, writer)
        assert client.kbdint_auth_requested() == ""

    def test_banner_received_feeds_reader(self):
        reader = SSHReader()
        writer = SSHWriter(peername=("host", 22))
        client = SSHTelix(reader, writer)
        client.banner_received("Welcome!\n", "en")
        assert not reader._buffer.empty()


@pytest.mark.asyncio
async def test_run_ssh_client_delivers_prompt_without_newline():
    """Chunks without trailing newlines (e.g. shell prompts) are delivered to the reader."""
    from unittest.mock import AsyncMock, MagicMock, patch

    chunks = ["*** System restart required ***\r\n", "user@host:~$ "]
    chunk_idx = 0
    delivered: list[str] = []
    eof_event = asyncio.Event()

    async def fake_read(n: int = -1) -> str:
        nonlocal chunk_idx
        if chunk_idx < len(chunks):
            data = chunks[chunk_idx]
            chunk_idx += 1
            return data
        await eof_event.wait()
        return ""

    fake_stdout = MagicMock()
    fake_stdout.read = fake_read

    fake_process = MagicMock()
    fake_process.stdout = fake_stdout
    fake_process.__aenter__ = AsyncMock(return_value=fake_process)
    fake_process.__aexit__ = AsyncMock(return_value=None)

    fake_conn = MagicMock()
    fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_conn.__aexit__ = AsyncMock(return_value=None)
    fake_conn.create_process = MagicMock(return_value=fake_process)

    async def fake_shell(reader: SSHReader, writer: object) -> None:
        while True:
            chunk = await reader.read()
            if not chunk:
                break
            delivered.append(chunk)

    with (
        patch("asyncssh.connect", return_value=fake_conn),
        patch("shutil.get_terminal_size", return_value=(80, 24)),
    ):
        from telix.ssh_client import run_ssh_client

        task = asyncio.ensure_future(
            run_ssh_client(
                host="host", port=22, username="user", key_file="", term_type="xterm", shell=fake_shell
            )
        )
        await asyncio.sleep(0.05)
        eof_event.set()
        await task

    assert delivered == chunks
