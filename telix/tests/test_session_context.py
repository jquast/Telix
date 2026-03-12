"""Tests for telix.session_context."""

from __future__ import annotations

import io
import types
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from telix.session_context import TelixSessionContext


def test_ansi_keys_default_false():
    """New TelixSessionContext has repl.ansi_keys == False."""
    ctx = TelixSessionContext()
    assert ctx.repl.ansi_keys is False


def test_typescript_file_preserved():
    """typescript_file passed to TelixSessionContext is stored on the instance."""
    f = io.StringIO()
    ctx = TelixSessionContext(typescript_file=f)
    assert ctx.typescript_file is f


def test_sub_objects_created():
    """All sub-objects exist with correct defaults on a fresh TelixSessionContext."""
    ctx = TelixSessionContext()
    assert ctx.room.current == ""
    assert ctx.walk.randomwalk_active is False
    assert ctx.macros.defs == []
    assert ctx.triggers.rules == []
    assert ctx.highlights.captures == {}
    assert ctx.chat.unread == 0
    assert ctx.gmcp.dirty is False
    assert ctx.progress.configs == []
    assert ctx.repl.enabled is False
    assert ctx.prompt.wait_fn is None
    assert ctx.scripts.manager is None


def test_mark_macros_dirty():
    """mark_macros_dirty sets macros.macros_dirty and schedules a flush."""
    ctx = TelixSessionContext()
    with patch.object(ctx, "schedule_flush") as mock_flush:
        ctx.mark_macros_dirty()
    assert ctx.macros.dirty is True
    mock_flush.assert_called_once()


def test_mark_triggers_dirty():
    """mark_triggers_dirty sets triggers.triggers_dirty and schedules a flush."""
    ctx = TelixSessionContext()
    with patch.object(ctx, "schedule_flush") as mock_flush:
        ctx.mark_triggers_dirty()
    assert ctx.triggers.dirty is True
    mock_flush.assert_called_once()


def test_mark_gmcp_dirty():
    """mark_gmcp_dirty sets gmcp.gmcp_dirty and schedules a flush."""
    ctx = TelixSessionContext()
    with patch.object(ctx, "schedule_flush") as mock_flush:
        ctx.mark_gmcp_dirty()
    assert ctx.gmcp.dirty is True
    mock_flush.assert_called_once()


def test_schedule_flush_no_running_loop():
    """schedule_flush is a no-op when there is no running event loop."""
    ctx = TelixSessionContext()
    ctx.schedule_flush()
    assert ctx.save_timer is None


@pytest.mark.asyncio
async def test_schedule_flush_sets_timer():
    """schedule_flush installs a call_later timer when a loop is running."""
    ctx = TelixSessionContext()
    ctx.schedule_flush()
    assert ctx.save_timer is not None
    ctx.save_timer.cancel()
    ctx.save_timer = None


@pytest.mark.asyncio
async def test_schedule_flush_idempotent():
    """schedule_flush does nothing if a timer is already pending."""
    ctx = TelixSessionContext()
    ctx.schedule_flush()
    first_timer = ctx.save_timer
    ctx.schedule_flush()
    assert ctx.save_timer is first_timer
    ctx.save_timer.cancel()
    ctx.save_timer = None


def test_flush_timestamps_saves_macros(tmp_path):
    """flush_timestamps calls save_macros when macros are dirty."""
    ctx = TelixSessionContext(session_key="host:1234")
    ctx.macros.file = str(tmp_path / "macros.json")
    ctx.macros.dirty = True
    ctx.macros.defs = [types.SimpleNamespace()]

    with patch("telix.session_context.macros.save_macros") as mock_save:
        ctx.flush_timestamps()

    mock_save.assert_called_once_with(ctx.macros.file, ctx.macros.defs, "host:1234")
    assert ctx.macros.dirty is False


def test_flush_timestamps_saves_triggers(tmp_path):
    """flush_timestamps calls save_triggers when triggers are dirty."""
    ctx = TelixSessionContext(session_key="host:1234")
    ctx.triggers.file = str(tmp_path / "triggers.json")
    ctx.triggers.dirty = True
    ctx.triggers.rules = [types.SimpleNamespace()]

    with patch("telix.session_context.trigger.save_triggers") as mock_save:
        ctx.flush_timestamps()

    mock_save.assert_called_once_with(ctx.triggers.file, ctx.triggers.rules, "host:1234")
    assert ctx.triggers.dirty is False


def test_flush_timestamps_saves_gmcp(tmp_path):
    """flush_timestamps calls save_gmcp_snapshot when GMCP is dirty."""
    ctx = TelixSessionContext(session_key="host:1234", gmcp_data={"pkg": {}})
    ctx.gmcp.snapshot_file = str(tmp_path / "gmcp.json")
    ctx.gmcp.dirty = True

    with patch("telix.session_context.gmcp_snapshot.save_gmcp_snapshot") as mock_save:
        ctx.flush_timestamps()

    mock_save.assert_called_once_with(ctx.gmcp.snapshot_file, "host:1234", ctx.gmcp_data)
    assert ctx.gmcp.dirty is False


def test_flush_timestamps_skips_when_not_dirty():
    """flush_timestamps does not call save functions when nothing is dirty."""
    ctx = TelixSessionContext()
    with (
        patch("telix.session_context.macros.save_macros") as mock_macros,
        patch("telix.session_context.trigger.save_triggers") as mock_triggers,
        patch("telix.session_context.gmcp_snapshot.save_gmcp_snapshot") as mock_gmcp,
    ):
        ctx.flush_timestamps()

    mock_macros.assert_not_called()
    mock_triggers.assert_not_called()
    mock_gmcp.assert_not_called()


@pytest.mark.asyncio
async def test_close_cancels_walk_tasks():
    """Close() cancels pending discover and randomwalk tasks."""
    ctx = TelixSessionContext()
    discover_task = asyncio.ensure_future(asyncio.sleep(100))
    randomwalk_task = asyncio.ensure_future(asyncio.sleep(100))
    ctx.walk.discover_task = discover_task
    ctx.walk.randomwalk_task = randomwalk_task

    ctx.close()
    await asyncio.sleep(0)  # let the event loop process cancellations

    assert discover_task.cancelled()
    assert randomwalk_task.cancelled()
    assert ctx.walk.discover_task is None
    assert ctx.walk.randomwalk_task is None


def test_close_stops_script_manager():
    """Close() calls stop_script on the script manager if one exists."""
    ctx = TelixSessionContext()
    mock_sm = MagicMock()
    ctx.scripts.manager = mock_sm

    with patch.object(ctx, "flush_timestamps"):
        ctx.close()

    mock_sm.stop_script.assert_called_once_with(None)
    assert ctx.scripts.manager is None


def test_close_clears_typescript_file():
    """Close() sets typescript_file to None so post-close code does not write to a closed file."""
    ctx = TelixSessionContext()
    ctx.typescript_file = MagicMock()

    with patch.object(ctx, "flush_timestamps"):
        ctx.close()

    assert ctx.typescript_file is None


def test_close_clears_prompt_echo():
    """Close() clears prompt.echo so scripts fall back to logging after session ends."""
    ctx = TelixSessionContext()
    ctx.prompt.echo = MagicMock()

    with patch.object(ctx, "flush_timestamps"):
        ctx.close()

    assert ctx.prompt.echo is None


def test_flush_timestamps_sync():
    """flush_timestamps_sync clears save_timer and delegates to flush_timestamps."""
    ctx = TelixSessionContext()
    ctx.save_timer = MagicMock()

    with patch.object(ctx, "flush_timestamps") as mock_flush:
        ctx.flush_timestamps_sync()

    assert ctx.save_timer is None
    mock_flush.assert_called_once()
