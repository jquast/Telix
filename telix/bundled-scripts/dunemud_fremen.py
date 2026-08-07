"""
DuneMUD automation: hunting, looting, and background job loops.

Run ``await fremen`` to schedule background jobs.  Run ``await fremen.hunt`` as a 'Room change command', and ``await
fremen.loot`` to evaluate room contents for looting (triggered by hunt).
"""

from __future__ import annotations

import re
import asyncio

from telix.scripts import ScriptContext

BG_TASKS = ["dunemud_fremen.fungus_loop", "dunemud_fremen.drink_loop", "dunemud_fremen.mode_loop"]

mud_lock = asyncio.Lock()
things_seen: dict[str, Thing] = {}

# 'get all <item>' depending on value ratio to current level
LEVEL_ITEM_RATIO = 50

# shouldn't take more than this long to kill something!
KILL_TIMEOUT = 300

RE_WEARING = re.compile(r"is (?:not )?wearing (?::|anything)", re.MULTILINE)
RE_ITEM_VALUE = re.compile(r"appears to be worth between (\d+) and (\d+) solaris\.", re.MULTILINE)
RE_TOKENS = r"^A token ring|^\w+ (\w+ )*token"
RE_SOCKETS = r"^A level \d+.*(amplifier|enhancer|shield|reactor|anti-gravitizer|generator)"
RE_SOLARIS = r"^1 solari|^\d+ solaris|^\w+ times '\d+ solaris"
RE_CONS_LEVEL = r"\((\d+)\)\s*$"
RE_CORPSE = r"corpse of(?! all)|\w+ times 'corpse"
RE_SALVAGE = r"^The metal remains"
RE_DRIED_CORPSE = r"dried up corpse"
RE_MISSING_GLANDS = r"missing glands"
RE_KILL_OVER = (
    r"died\.|^You killed|^What\?|^Kill what \?|^Your legs run away with you!"
    r"|^You cannot attack|^Fights aren\'t allowed here\."
)
RE_CANNOT_ATTACK = r"^You cannot attack|^Fights aren\'t allowed here\."
RE_AUTO_ATTACK = r"^You turn to attack"
RE_MANY_TIMES = r"\w+ times '(.+)'"
RE_SOME_CLASS = r"(.+) \(.+\)"
RE_SOME_BRACK = r"(.+) \[.+\]"
RE_SOME_CONDT = r"(.+) in \w+ condition"
RE_FROM_PLACE = r"(.+) from .+"
MSG_TOO_FAST = "Spamming this command hurts the mud. Wait a second."
ITEMS_ALWAYS_TAKE = "melon"


class Thing:
    def __init__(self, name: str, monster: bool = False):
        self.name: str = name
        self.monster: bool = monster
        self.item: bool = not monster
        self.description: str = ""
        self.value: int = 0
        self.ignored: bool = False

    def __str__(self):
        return self.name

    @property
    def short_name(self):
        return self.reduce_name(self.name)

    @property
    def target(self):
        words = self.short_name.split()
        if len(words) > 2 and ({"a", "an"} & set(words[2:]) or words[2] == "being"):
            return words[1].lower().rstrip("!.")
        return words[-1].lower().rstrip("!.")

    @property
    def is_unknown(self):
        # candidates for identification -- monster or item?
        return not any(
            (
                self.description,
                self.is_corpse,
                self.is_salvage,
                self.is_token,
                self.is_solaris,
                self.is_socket,
                self.is_board,
            )
        )

    @property
    def is_board(self):
        return self.item and self.target == "board"

    @property
    def is_corpse(self):
        return self.item and re.search(RE_CORPSE, self.name, re.IGNORECASE)

    @property
    def is_salvage(self):
        return self.item and re.search(RE_SALVAGE, self.name, re.IGNORECASE)

    @property
    def is_token(self):
        return self.item and re.match(RE_TOKENS, self.short_name)

    @property
    def is_solaris(self):
        return self.item and re.match(RE_SOLARIS, self.name)

    @property
    def is_socket(self):
        return self.item and re.match(RE_SOCKETS, self.name, re.IGNORECASE)

    @staticmethod
    def reduce_name(name):
        # "Two times 'Large seal'" -> 'Large seal'
        # 'Two bladed axe in perfect condition (Axes and Maces)' -> 'Two bladed axe'
        result = name
        for pattern in (RE_MANY_TIMES, RE_SOME_CLASS, RE_SOME_BRACK, RE_SOME_CONDT, RE_FROM_PLACE):
            if m := re.match(pattern, result):
                result = m.group(1)
        return result


class Things:
    def __init__(self, things: list[Thing]):
        self.things = things

    def __iter__(self):
        return iter(self.things)

    def __len__(self):
        return len(self.things)

    def __getitem__(self, index):
        return self.things[index]

    def __bool__(self):
        return bool(self.things)

    def __add__(self, other):
        return Things([*self.things, *other.things])

    def append(self, value):
        self.things.append(value)

    @property
    def monsters(self) -> Things:
        return Things([t for t in self.things if t.monster and not t.ignored])

    @property
    def items(self) -> Things:
        return Things([t for t in self.things if t.item])

    @property
    def corpses(self) -> Things:
        return Things([t for t in self.things if t.is_corpse])

    @property
    def salvage(self) -> Things:
        return Things([t for t in self.things if t.is_salvage])

    @property
    def tokens(self) -> Things:
        return Things([t for t in self.things if t.is_token])

    @property
    def solaris(self) -> Things:
        return Things([t for t in self.things if t.is_solaris])

    @property
    def sockets(self) -> Things:
        return Things([t for t in self.things if t.is_socket])

    def contains(self, name: str) -> bool:
        return any(t.name == name for t in self.things)

    def get_one(self, name: str) -> Thing | None:
        for t in self.things:
            if t.name == name:
                return t

    @staticmethod
    def number_target(target, count):
        return f"{target} {count}" if count else target

    @property
    def numbered(self) -> list[tuple[Thing, str]]:
        seen: dict[str, int] = {}
        result = []
        for thing in self.things:
            count = seen.get(thing.target, 0)
            seen[thing.target] = count + 1
            result.append((thing, self.number_target(thing.target, count)))
        return result

    @property
    def targets(self) -> list[str]:
        return [nt for t, nt in self.numbered if t.item]


async def list_things(ctx: ScriptContext, prev_things: Things | None = None) -> Things:
    prev_things = prev_things or Things([])
    result: Things = Things([])
    output = ctx.output(clear=False).splitlines()[1:-1]
    if output:
        ctx.debug(f"list_things: evaluating output:{len(output)} (prev_things:{len(prev_things)})")
    target_counts: dict[str, int] = {}
    for candidate in output:
        if re.search(RE_MISSING_GLANDS, candidate, re.IGNORECASE):
            continue
        if re.search(RE_DRIED_CORPSE, candidate, re.IGNORECASE):
            continue
        thing = None if not prev_things else prev_things.get_one(candidate)
        if thing is None:
            # not previously identified,
            # Q: how can we tell whether it is a monster?
            # A: if it is wearing something
            thing = Thing(name=candidate)
        numbered_target = Things.number_target(thing.target, target_counts.get(thing.target, 0))
        target_counts[thing.target] = target_counts.get(thing.target, 0) + 1
        if thing.is_unknown:
            cached = things_seen.get(thing.short_name)
            if cached is not None:
                thing.description = cached.description
                thing.monster = cached.monster
            else:
                for _existing_thing in prev_things + result:
                    if _existing_thing.target == thing.target:
                        thing.description = _existing_thing.description
                        thing.monster = _existing_thing.monster
                        break
                else:
                    ctx.output(clear=True)
                    await ctx.send(f"look at {numbered_target}")
                    thing.description = ctx.output()
                    thing.monster = bool(RE_WEARING.search(thing.description))
                things_seen[thing.short_name] = thing
        result.append(thing)
    return Things(result)


async def nice_command(ctx: ScriptContext, command: str) -> None:
    # send a command and await prompt, retry after pause when "too fast" message received
    num_retries = 3
    for n in range(num_retries):
        ctx.output(clear=True)
        await ctx.send(command)
        if MSG_TOO_FAST not in ctx.output(clear=False):
            return
        # sleep before retry
        await asyncio.sleep(n + 1)
    return


async def should_kill(ctx: ScriptContext, target: Thing) -> bool:
    # arbitrary math that decides whether we shall kill this thing
    lvl_floor_mult = 0.75
    lvl_match_mul = 1.8
    lvl_ceil_mult = 2.8
    water_floor_pct = 0.4
    water_ceil_pct = 0.75

    target_level = await get_target_level(ctx, target)
    water_pct = ctx.gmcp_get("Char.Guild.Stats.Water%")
    # easy target,
    my_level = ctx.gmcp_get("Char.Status.level")
    if my_level * lvl_floor_mult > target_level:
        return True

    # medium target, attack if we have *some* water,
    if my_level * lvl_match_mul >= target_level and water_pct > water_floor_pct:
        return True

    # difficult target, attack if we have plenty of water,
    if my_level * lvl_ceil_mult > target_level and water_pct > water_ceil_pct:
        return True

    ctx.print(
        f"should_kill {target.target} too strong: {target_level / my_level:2.2f}, water_pct={water_pct * 100:2.2f}!"
    )
    return False


async def get_target_level(ctx: ScriptContext, monster: Thing) -> float:
    # get target's (level number) using 'consider -q' command
    await nice_command(ctx, f"consider -q {monster.target}")
    lines = ctx.output().splitlines()
    for line in lines:
        if m := re.search(RE_CONS_LEVEL, line):
            return float(int(m.group(1)))
    # on error return maximum level, eg. "impossible"
    return float("inf")


async def maybe_pillage_corpses(ctx: ScriptContext, things: Things) -> Things:
    drank = 0
    torched = 0
    while True:
        prev_corpse_names = {t.name for t in things.corpses}
        for corpse in things.corpses:
            if ctx.gmcp_get("Water%") < 1.0 or ctx.gmcp_get("Adrenaline%") < 1.0:
                await ctx.send("degland corpse")
                await ctx.send("distill corpse")
                drank += 1
            else:
                await ctx.send("torch corpse")
                torched += 1
        for _ in things.salvage:
            await ctx.send("salvage corpse")

        if things.salvage:
            await ctx.send("compact all salvage")

        if drank or torched:
            drank = torched = 0
            ctx.output(clear=True)
            await ctx.send("gl")
            things = await list_things(ctx, things)
            if {t.name for t in things.corpses} == prev_corpse_names:
                break
            continue

        break

    return things


async def maybe_get_tokens(ctx: ScriptContext, things: Things) -> Things:
    if things.tokens:
        await ctx.send("get all token")
    return Things([t for t in things if not t.is_token])


async def maybe_get_solaris(ctx: ScriptContext, things: Things) -> Things:
    if things.solaris:
        await ctx.send("get all solaris")
    return Things([t for t in things if not t.is_solaris])


async def maybe_get_sockets(ctx: ScriptContext, things: Things) -> Things:
    for socket in things.sockets:
        await ctx.send(f"get {socket.target}")
    if things.sockets:
        await ctx.send("store all sockets")
    return Things([t for t in things if not t.is_socket])


async def loot(ctx: ScriptContext, things: Things = None):
    async with mud_lock:
        return await _do_loot(ctx, things)


async def maybe_get_things(ctx: ScriptContext, things: Things) -> Things:
    for thing, numbered_target in things.numbered:
        if thing.monster:
            continue
        value = 0
        if prev_seen := things_seen.get(thing.short_name):
            value = prev_seen.value
        if not value and thing.target not in ITEMS_ALWAYS_TAKE:
            # how valuable is this thing?
            await ctx.send(f"evaluate {numbered_target}")
            if m := RE_ITEM_VALUE.search(ctx.output()):
                try:
                    value = int(m.group(1)) + ((int(m.group(2)) - int(m.group(1))) // 2)
                except ValueError as exc:
                    ctx.print(f"ValueError: {exc}")
                    value = 0
            thing.value = value
            things_seen.setdefault(thing.short_name, thing).value = value
        if value > ctx.gmcp_get("Char.Status.level") * LEVEL_ITEM_RATIO or thing.target in ITEMS_ALWAYS_TAKE:
            await ctx.send(f"get all {thing.target}")
            things = Things([t for t in things if t.target != thing.target])
        else:
            ctx.debug(f"Value too low({value})")
    return things


async def _do_loot(ctx: ScriptContext, things: Things = None):
    # conditionally clear output and issue glance command,
    if things is None:
        ctx.output(clear=True)
        await ctx.send("gl")
        things = await list_things(ctx)

    ctx.debug(f"loot: briefly evaluated {len(things)} things")
    things = await maybe_pillage_corpses(ctx, things)
    things = await maybe_get_tokens(ctx, things)
    things = await maybe_get_solaris(ctx, things)
    things = await maybe_get_sockets(ctx, things)
    things = await maybe_get_things(ctx, things)

    remaining_things = ", ".join(things.targets)

    if remaining_things:
        # compact all that remains ..
        await ctx.send("compact -q all in room")

    return things


async def drink_loop(ctx: ScriptContext):
    # automatically drink water when necessary
    while True:
        await ctx.conditions_met(("hp%", "<", 100), ("Water%", ">", 0))
        async with mud_lock:
            await ctx.send("dw")


async def mode_loop(ctx: ScriptContext):
    # automatically set 'rage' mode when adrenaline allows
    for _ in range(10):
        ctx.print(ctx.gmcp_get("Mode"), ctx.gmcp_get("Adrenaline%"))
        await ctx.conditions_met(("Mode", "!=", "Rage"), ("Adrenaline%", ">", 0))
        async with mud_lock:
            await ctx.send("setmode rage")
        await asyncio.sleep(2)


async def fungus_loop(ctx: ScriptContext):
    # automatically apply poison to crysknife when available
    while True:
        if await ctx.wait_for("-{ More poison has been excreted by the fungus. }-"):
            async with mud_lock:
                if ctx.gmcp_get("Char.Status.level") < 30:
                    await ctx.send("poison crysknife")
                else:
                    await ctx.send("poison crysknife slow")


async def hunt(ctx: ScriptContext):
    async with mud_lock:
        # clear output buffer and send glance to "evaluate" this room,
        ctx.output()
        await ctx.send("gl")
        things = await list_things(ctx)

        # attack targets, re-scanning after each pass to catch grouped
        # monsters ("Two times 'X'") that are a single Thing entry.
        total_kills = 0
        while things.monsters:
            killed = 0
            for mons in things.monsters:
                if await should_kill(ctx, mons):
                    await ctx.send(f"kill {mons.target}")
                    kill_result = await ctx.wait_for(RE_KILL_OVER, timeout=KILL_TIMEOUT)
                    if kill_result and re.search(RE_CANNOT_ATTACK, kill_result.group(0)):
                        ctx.print(f"Cannot attack {mons.target}, ignoring")
                        mons.ignored = True
                        continue
                    killed += 1
                    # drain auto-attacks on remaining monsters
                    while await ctx.wait_for(RE_AUTO_ATTACK, timeout=0.25):
                        await ctx.wait_for(RE_KILL_OVER, timeout=KILL_TIMEOUT)
                        killed += 1
            if not killed:
                break
            total_kills += killed
            ctx.output()
            await ctx.send("gl")
            things = await list_things(ctx, things)

        # process/loot
        things = await _do_loot(ctx, things)


async def run(ctx: ScriptContext):
    # start or re-schedule fremen background tasks
    stopped = 0
    for script_name in (_s for _s in ctx.running_scripts if _s in BG_TASKS):
        await ctx.send(f"`stopscript {script_name}`")
        stopped += 1
    if stopped:
        await asyncio.sleep(0.25)
    for script_name in BG_TASKS:
        await ctx.send(f"`async {script_name}`")

    # show new running tasks
    await ctx.send("`scripts`")

    # brief output when moving
    await ctx.send("set brief")
