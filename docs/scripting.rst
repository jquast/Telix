=========
Scripting
=========

Telix scripts are Python files that run asynchronously using the asyncio
interface.  They are useful for logic that goes beyond the "find pattern and
respond" capability of Triggers_: hunting loops, healing routines, mapping
runs, or any sequence that depends on timing or server output.

Triggering scripts
------------------

Scripts are started by the `` `async` `` or `` `await` `` commands (see
`Launching scripts`_ below).

Run a script and wait for completion::

    await module.func_name

Run a script asynchronously (in the background)::

    async module.func_name

These commands can be used anywhere that Commands_ may be used:

- **REPL**: type `` `async module.fn` `` at the prompt
- **Trigger reply field**: set reply as `` `async module.fn` ``
- **Macro text**: include `` `async module.fn` `` in macro text
- **autowalk** and **autodiscover**: include `` `async module.fn` `` in room-change command.

Quickstart
~~~~~~~~~~

Create a file named ``demo.py`` in the current directory where ``telix`` is
launched or in ``~/.config/telix/scripts/``::

    from telix.scripts import ScriptContext

    async def run(ctx: ScriptContext) -> None:
        room = ctx.room
        ctx.print(f"[demo] You are in: {room.name} ({room.area})")
        ctx.print(f"[demo] Exits: {', '.join(room.exits)}")

Then in the Telix REPL, run it::

    `async demo`

Example output::

    [demo] You are in: Mayor's office (caladan)
    [demo] Exits: west

Lifecycle
---------

If you wish, multiple scripts can run at the same time. Asynchronous programming
is very useful but can also get out of control, and may accidentally flood the server
with bad commands.

To stop everything immediately, press **Alt + Q**.

Launching scripts
~~~~~~~~~~~~~~~~~

`` `async NAME` ``
    Start a script in the background and return immediately.  The script runs
    concurrently with everything else::

        `async combat.hunt`

`` `await NAME` ``
    Start a script and block until it finishes before continuing.  Useful
    inside ``roomcmd`` sequences where you need the script to complete before
    the walk moves on::

        `await combat.hunt`

Search path
~~~~~~~~~~~

Telix looks for scripts in this order:

1. Current working directory -- launch telix from your project folder and
   scripts there are found automatically.
2. ``~/.config/telix/scripts/`` -- a good place for scripts you want
   available everywhere.

The last part of the script name is the function to call; everything before
it is the file to load:

- `` `async demo` `` -- loads ``demo.py``, calls ``run(ctx)``
- `` `async combat.hunt` `` -- loads ``combat.py``, calls ``hunt(ctx)``

Reloading
~~~~~~~~~

Every time a script is launched, telix will check whether the file has been modified and reload if
necessary. Save a script file at any time and then call it to see any new changes (or errors!).

Listing active scripts
~~~~~~~~~~~~~~~~~~~~~~

`` `scripts` ``
    Show the names of all currently running scripts::

        [scripts] running: combat.hunt
        [scripts] running: healer.top_up

    If nothing is running: ``[scripts] no scripts running``.

Stopping
~~~~~~~~

`` `stopscript` ``
    Stop all running scripts.  Each name is printed as it stops:
    ``[stopscript] stopped: combat.hunt``.

    This command is bound to **Alt + Q** by default.

`` `stopscript combat.hunt` ``
    Stop only the named script.

Chaining
~~~~~~~~

You could launch a another script by sending a command through ``ctx.send``::

    await ctx.send("`async hunt`")

Or run another script to completion::

    await ctx.send("`await hunt`")

As python scripts, you may also just import and call functions directly::

    import combat
    await combat.hunt(ctx, "goblin")

IDE support
-----------

Adding a type annotation to ``ctx`` should provide auto-complete and type checking to an IDE, or
otherwise use a linter (mypy, flake8) is suggested to catch mistakes before running scripts::

    from __future__ import annotations
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from telix.scripts import ScriptContext

    async def run(ctx: ScriptContext) -> None:
        ctx.send("look")          # Pylance/mypy: error -- missing await
        await ctx.send("look")    # correct

The ``ctx`` object
------------------

Every script receives a ``ctx`` argument. This is a "God variable", and provides
access to all known information about the MUD Session and scripting capabilities
of Telix.

Sending commands
~~~~~~~~~~~~~~~~

``await ctx.send(line)``
    Send a command to the server.  Supports the same syntax as the REPL:

    - ``;`` between commands waits for the server's prompt before sending the next
    - ``|`` sends immediately without waiting
    - A leading number repeats: ``3north`` will send ``north`` times

    Backtick directives like `` `async` `` and `` `until` `` are handled by the client, not sent to
    the server.  See Commands_ for the full list of available backtick commands.

Prompt waiting
~~~~~~~~~~~~~~

``await ctx.prompt(timeout=30.0)``
    Wait for the server's next prompt.  Returns ``True`` if it arrived within
    the timeout, ``False`` otherwise.

``await ctx.prompts(n, timeout=30.0)``
    Wait for *n* prompts in a row.  Useful for pacing a sequence of commands.

Output buffering
~~~~~~~~~~~~~~~~

``ctx.output(clear=True)``
    Return everything the server has sent since the script started, as a single
    string.  The buffer is cleared afterwards by default, so the next call only
    shows new output.  Pass ``clear=False`` to read without clearing.

``ctx.turns(n=5)``
    Return the last *n* chunks of server output, split at each prompt.  Useful
    when you want to inspect the response to a specific command.

Pattern matching
~~~~~~~~~~~~~~~~

``await ctx.wait_for(pattern, timeout=30.0)``
    Wait for a line of server output matching *pattern* (a regular expression).
    Returns the match object when found, or ``None`` on timeout.

Condition polling
~~~~~~~~~~~~~~~~~

``await ctx.condition_met(key, op, threshold, poll_interval=0.25)``
    Wait until a numeric condition becomes true, checking every
    *poll_interval* seconds.

    *key* can be ``"HP%"``, ``"MP%"``, ``"HP"``, ``"MP"``, or the name of any
    highlight capture variable.  *op* is one of ``">"``, ``"<"``, ``">="``,
    ``"<="``, ``"="``.

    Works well inside :func:`asyncio.wait` -- see `Multi-condition waits`_ below.

Terminal output
~~~~~~~~~~~~~~~

``ctx.print(*args, sep=" ")``
    Print a message to the terminal in cyan.  Works like Python's built-in
    ``print``: pass multiple values and they are joined with *sep*.

``ctx.log(msg)``
    Write a message to the telix log file at INFO level.

GMCP data
~~~~~~~~~

``ctx.gmcp``
    The full GMCP data dictionary, as received from the server.

``ctx.gmcp_get(dotted_path)``
    Read a value out of the GMCP data by path, e.g.
    ``ctx.gmcp_get("Char.Vitals.hp")``.  Returns ``None`` if not found.

``await ctx.gmcp_changed(package, timeout=30.0)``
    Wait until the next GMCP packet for *package* is received.  Returns
    ``True`` if a packet arrived within the timeout, ``False`` otherwise::

        async def watch_vitals(ctx: ScriptContext) -> None:
            while True:
                if not await ctx.gmcp_changed("Char.Vitals", timeout=60.0):
                    break
                hp = ctx.gmcp_get("Char.Vitals.hp")
                ctx.print(f"[vitals] HP: {hp}")

Room graph
~~~~~~~~~~

``ctx.room_id``
    The current room's number, as a string.

``ctx.previous_room_id``
    The number of the room you were in before the current one.

``ctx.room``
    The current :class:`~telix.rooms.Room` object, or ``None`` if telix does
    not yet know what room you are in.  The room object has:

    - ``room.name`` -- room name string
    - ``room.area`` -- area name string
    - ``room.exits`` -- ``{direction: room_num}`` dict of known exits

``ctx.room_graph``
    The full :class:`~telix.rooms.RoomStore` -- all rooms telix has mapped for
    this session.

``ctx.get_room(num)``
    Look up any room by its number.

``ctx.find_path(dst)``
    Find directions from the current room to *dst*.  Returns a list of
    direction strings, or ``None`` if no route is known.

``await ctx.room_changed(timeout=30.0)``
    Wait until you move to a new room.  Returns ``True`` on a room change,
    ``False`` on timeout::

        async def tracker(ctx: ScriptContext) -> None:
            while True:
                if not await ctx.room_changed(timeout=60.0):
                    break
                ctx.print(f"[tracker] {ctx.previous_room_id} -> {ctx.room_id}")

``ctx.captures``
    The current value of each highlight capture variable, as a dictionary.

``ctx.capture_log``
    The full history of every capture event -- useful for tracking how a value
    has changed over time, e.g. HP across several combat rounds.

Session identity
~~~~~~~~~~~~~~~~

``ctx.session_key``
    A string identifying the current connection, in ``"host:port"`` form.
    Useful when a script needs to save data per server.

Chat
~~~~

``ctx.chat_messages``
    All chat and tell messages received this session.

``ctx.chat_unread``
    How many messages have arrived since the last time they were read.

``ctx.chat_channels``
    The list of available chat channels.

Walk control
~~~~~~~~~~~~

``ctx.walk_active``
    ``True`` if autodiscover, randomwalk, or travel is currently running.

``ctx.stop_walk()``
    Stop any active walk.  Call this before sending your own movement commands
    to avoid conflicts::

        async def scout(ctx: ScriptContext, *args: str) -> None:
            if ctx.walk_active:
                ctx.stop_walk()
                await ctx.prompt()
            await ctx.send("look")

Arguments
---------

Anything you type after the script name is passed to the function as
positional string arguments::

    `async rooms.goto 12345`
    `async combat.hunt goblin "dark lair"`

In the script, receive them via ``*args``::

    async def goto(ctx: ScriptContext, *args: str) -> None:
        room_id = args[0] if args else ""
        ...

    async def hunt(ctx: ScriptContext, *args: str) -> None:
        target = args[0] if args else "goblin"
        place = args[1] if len(args) > 1 else ""
        ...

Multi-condition waits
---------------------

Sometimes you want to react to whichever thing happens first -- the enemy
dies, you flee, or your HP drops too low.  :func:`asyncio.wait` with
``FIRST_COMPLETED`` handles this: give it a list of things to watch for, and
it returns as soon as one of them fires::

    import asyncio
    from telix.scripts import ScriptContext

    async def hunt(ctx: ScriptContext, *args: str) -> None:
        target = args[0] if args else "goblin"
        await ctx.send(f"kill {target}")
        done, pending = await asyncio.wait(
            [
                asyncio.ensure_future(ctx.wait_for(f"{target} has died")),
                asyncio.ensure_future(ctx.wait_for("You flee")),
                asyncio.ensure_future(ctx.condition_met("HP%", "<", 25)),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        ctx.print("[hunt] done")

Shared state
------------

Scripts that run simultaneously can share data by keeping it in a separate
module.  Any script that imports that module gets the same object, so changes
made by one script are immediately visible to another.

For example, create ``state.py``::

    monsters: list[str] = []
    kills: int = 0

Then any number of scripts can read and write it::

    import state

    async def run(ctx: ScriptContext) -> None:
        state.monsters.append("goblin")
        ctx.print(f"kills so far: {state.kills}")

One thing to be aware of: if you edit a file and run it again, telix reloads
it automatically.  Any variables defined at the top of that file go back to
their initial values, so accumulated data is lost.  If you need data to
survive a reload, keep it in a file or database instead.

Complete examples
-----------------

Room info script::

    from telix.scripts import ScriptContext

    async def run(ctx: ScriptContext) -> None:
        room = ctx.room
        if room is None:
            ctx.print("[demo] No room data")
            return
        ctx.print(f"[demo] Room: {room.name} ({room.area})")
        ctx.print(f"[demo] Exits: {', '.join(room.exits)}")

Travel to a room by number::

    from telix.scripts import ScriptContext

    async def goto(ctx: ScriptContext, *args: str) -> None:
        if not args:
            ctx.print("[rooms] Usage: `async rooms.goto <room_id>`")
            return
        path = ctx.find_path(args[0])
        if path is None:
            ctx.print(f"[rooms] No path to {args[0]}")
            return
        await ctx.send(";".join(path))

Wait for a pattern then react::

    from telix.scripts import ScriptContext

    async def hunt(ctx: ScriptContext, *args: str) -> None:
        target = args[0] if args else "goblin"
        await ctx.send(f"consider {target}")
        m = await ctx.wait_for(r"seems? .* formidable|is? no match", timeout=5.0)
        if not m or "no match" not in m.group(0):
            ctx.print(f"[hunt] {target} too tough, skipping")
            return
        await ctx.send(f"kill {target}")
        ctx.print("[hunt] fighting!")
