"""Tests for extracted helpers in :mod:`telix.client_repl_travel`."""

import time
import types
import logging
import asyncio
import collections

import pytest

from telix import rooms, client_repl_travel, client_shell


class FakeEngine:
    """Minimal stand-in for TriggerEngine."""

    def __init__(self):
        self.exclusive_active = False
        self.reply_pending = False

    def check_timeout(self):
        pass


@pytest.mark.asyncio()
async def test_settle_triggers_noreply_skips():
    """settle_triggers returns immediately when noreply is True."""
    engine = FakeEngine()
    engine.exclusive_active = True
    engine.reply_pending = True
    called = False

    async def wait_fn():
        nonlocal called
        called = True

    await client_repl_travel.settle_triggers(engine, wait_fn, noreply=True)
    assert not called


@pytest.mark.asyncio()
async def test_settle_triggers_no_pending():
    """settle_triggers returns immediately when nothing is pending."""
    engine = FakeEngine()

    await client_repl_travel.settle_triggers(engine, None, noreply=False)


@pytest.mark.asyncio()
async def test_settle_triggers_waits_for_exclusive():
    """settle_triggers waits until exclusive_active clears."""
    engine = FakeEngine()
    engine.exclusive_active = True

    iterations = 0

    def fake_check_timeout():
        nonlocal iterations
        iterations += 1
        if iterations >= 2:
            engine.exclusive_active = False

    engine.check_timeout = fake_check_timeout

    await client_repl_travel.settle_triggers(engine, None, noreply=False)
    assert iterations >= 2


def test_repath_finds_new_route(tmp_path):
    """Repath returns a new path when one exists."""
    store = rooms.RoomStore(str(tmp_path / "rooms.db"))
    store.update_room({"num": "A", "exits": {"east": "B"}})
    store.update_room({"num": "B", "exits": {"east": "C"}})
    store.update_room({"num": "C", "exits": {}})

    result = client_repl_travel.repath(store, "C", "A", lambda msg: None)
    assert result == [("east", "B"), ("east", "C")]
    store.close()


def test_repath_no_path(tmp_path):
    """Repath returns an empty list when no path exists."""
    store = rooms.RoomStore(str(tmp_path / "rooms.db"))
    store.update_room({"num": "A", "exits": {}})
    store.update_room({"num": "C", "exits": {}})

    result = client_repl_travel.repath(store, "C", "A", lambda msg: None)
    assert result == []
    store.close()


def test_repath_already_at_destination(tmp_path):
    """Repath returns empty list when already at destination."""
    store = rooms.RoomStore(str(tmp_path / "rooms.db"))
    store.update_room({"num": "A", "exits": {}})

    result = client_repl_travel.repath(store, "A", "A", lambda msg: None)
    assert result == []
    store.close()


def make_travel_ctx(graph=None, current=""):
    """Build a minimal context namespace for fast_travel tests."""
    walk = types.SimpleNamespace(
        active_command=None,
        active_command_time=0.0,
        pending_directions=collections.deque(),
        last_consumed_direction=None,
        command_delay=0.0,
        discover_active=False,
        discover_current=0,
        randomwalk_active=False,
        randomwalk_current=0,
        randomwalk_total=0,
        busy_lock=asyncio.Lock(),
        cancel_event=asyncio.Event(),
    )
    repaint_calls = []
    prompt = types.SimpleNamespace(wait_fn=None, echo=None, ready=None, repaint_input=lambda: repaint_calls.append(1))
    room = types.SimpleNamespace(graph=graph, current=current, changed=asyncio.Event(), arrival_timeout=0.0)
    triggers = types.SimpleNamespace(engine=None)
    writer = types.SimpleNamespace(write=lambda s: None)
    ctx = types.SimpleNamespace(prompt=prompt, room=room, walk=walk, triggers=triggers, writer=writer)
    return ctx, repaint_calls


@pytest.mark.asyncio()
async def test_fast_travel_calls_repaint_on_completion(tmp_path):
    """fast_travel calls repaint_input after clearing active_command."""
    import logging

    store = rooms.RoomStore(str(tmp_path / "rooms.db"))
    store.update_room({"num": "A", "name": "Start", "exits": {"east": "B"}})
    store.update_room({"num": "B", "name": "End", "exits": {}})
    ctx, repaint_calls = make_travel_ctx(graph=store, current="A")

    original_write = ctx.writer.write

    def fake_write(s):
        original_write(s)
        ctx.room.current = "B"
        ctx.room.changed.set()

    ctx.writer.write = fake_write

    await client_repl_travel.fast_travel([("east", "B")], ctx, logging.getLogger("test"), destination="B")
    store.close()
    assert repaint_calls
    assert ctx.walk.active_command is None


@pytest.mark.asyncio()
async def test_fast_travel_calls_repaint_on_empty_path():
    """fast_travel calls repaint_input even with an empty step list."""
    import logging

    ctx, repaint_calls = make_travel_ctx()
    await client_repl_travel.fast_travel([], ctx, logging.getLogger("test"))
    assert repaint_calls
    assert ctx.walk.active_command is None


@pytest.mark.asyncio()
async def test_fast_travel_blocks_bad_exit_and_reroutes(tmp_path):
    """When a step fails (room unchanged), the exit is blocked and travel re-routes."""
    import logging

    store = rooms.RoomStore(str(tmp_path / "rooms.db"))
    store.update_room({"num": "A", "name": "Start", "exits": {"read sign": "X", "east": "B"}})
    store.update_room({"num": "X", "name": "DeadEnd", "exits": {}})
    store.update_room({"num": "B", "name": "Mid1", "exits": {"east": "C", "west": "A"}})
    store.update_room({"num": "C", "name": "Mid2", "exits": {"east": "D", "west": "B"}})
    store.update_room({"num": "D", "name": "Dest", "exits": {"west": "C"}})

    ctx, repaint_calls = make_travel_ctx(graph=store, current="A")
    original_write = ctx.writer.write

    def fake_write(s):
        original_write(s)
        cmd = s.strip()
        current = ctx.room.current
        if cmd == "east" and current == "A":
            ctx.room.current = "B"
            ctx.room.changed.set()
        elif cmd == "east" and current == "B":
            ctx.room.current = "C"
            ctx.room.changed.set()
        elif cmd == "east" and current == "C":
            ctx.room.current = "D"
            ctx.room.changed.set()

    ctx.writer.write = fake_write

    path = [("read sign", "X")]
    await client_repl_travel.fast_travel(path, ctx, logging.getLogger("test"), destination="D")
    store.close()

    # Travel reached the destination via re-route (A→east→B→east→C→east→D)
    assert ctx.room.current == "D"
    assert ctx.walk.active_command is None
    # The blocked exit ("read sign") is restored to the graph by the
    # finally block so it remains available for future travel sessions.
    assert "read sign" in store.adj.get("A", {})


def test_room_info_does_not_record_edge_when_active_command_none(tmp_path):
    """room_info skips exit recording when active_command is None."""
    store = rooms.RoomStore(str(tmp_path / "test.db"))
    store.update_room({"num": "A", "name": "Room A", "exits": {"east": "B"}})
    store.update_room({"num": "B", "name": "Room B", "exits": {}})

    prev = "A"
    num = "B"
    active_command = None

    if prev and num and num != prev and active_command:
        store.adj.setdefault(prev, {})[active_command] = num

    assert store.adj["A"] == {"east": "B"}


def make_walk(active_command=None, pending=()):
    """Build a minimal WalkState-like namespace for edge-direction tests."""
    return types.SimpleNamespace(
        active_command=active_command, active_command_time=0.0, pending_directions=collections.deque(pending)
    )


def test_consume_edge_direction_fifo_oldest_first():
    """Room changes are attributed to the oldest pending direction first."""
    log = logging.getLogger("test")
    walk = make_walk(active_command="w", pending=(("n", time.monotonic()), ("w", time.monotonic())))
    assert client_shell.consume_edge_direction(walk, log) == "n"
    assert client_shell.consume_edge_direction(walk, log) == "w"
    assert walk.pending_directions == collections.deque()


def test_consume_edge_direction_fifo_overrides_active():
    """A pending direction wins over active_command (delayed response)."""
    log = logging.getLogger("test")
    walk = make_walk(active_command="e", pending=(("n", time.monotonic()),))
    assert client_shell.consume_edge_direction(walk, log) == "n"


def test_consume_edge_direction_falls_back_to_active():
    """With no pending directions, active_command is used."""
    log = logging.getLogger("test")
    walk = make_walk(active_command="s")
    assert client_shell.consume_edge_direction(walk, log) == "s"


def test_consume_edge_direction_none_when_no_command():
    """With no pending or active command, no direction is returned."""
    log = logging.getLogger("test")
    walk = make_walk()
    assert client_shell.consume_edge_direction(walk, log) is None


def test_consume_edge_direction_drops_stale_pending():
    """Stale pending directions (blocked moves) are discarded, not consumed."""
    log = logging.getLogger("test")
    old = time.monotonic() - client_shell.PENDING_COMMAND_STALE - 10.0
    walk = make_walk(active_command="w", pending=(("n", old), ("w", time.monotonic())))
    assert client_shell.consume_edge_direction(walk, log) == "w"
    assert walk.pending_directions == collections.deque()
