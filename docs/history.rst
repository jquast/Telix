History
=======

0.1.12 -- unreleased

- enhancement: enable tab-completion of recently seen words in output
- bugfix: issuing multiple travel commands should allow canceling


0.1.11 -- 2026-08-07
--------------------

- enhancement: ``on_gmcp`` registration hooks for scripting
- enhancement: bundle ``discworld_rooms.py`` and ``dunemud_fremen.py`` demonstration scripts
- enhancement: Discworld mapping imports Quow's map database on first run, for instant access to pre-mapped rooms
- enhancement: triggers can hide matching lines from the display
- enhancement: new `` `cr` `` command to send a bare carriage return, e.g. to dismiss prompts
- enhancement: session editor gains "on-connect command" and "extra GMCP modules" fields
- bugfix: correct terminal window size is now reported to servers
- change: press any key, not just return, to stop any automatic commands

0.1.10 -- 2026-07-15
--------------------

- bugfix: gmcp and room identifiers for Discworld MUD
- bugfix: MUD TUI crashes due to CSS error
- enhancement: log Textualie exceptions and css linting
- ATASCII raw mode ESC/return fixes

0.1.9 -- 2026-07-13
-------------------

- enhancement: add raw tcp support and address a few session manager TUI layout issues

0.1.8 -- 2026-06-26
-------------------

- enhcancement: add ``--local-echo`` or ``--remote-echo`` for retrocomputer BBS's that require it.
- enhancement: add ``--graphics-font`` option, uses kitty or sixel for retrocomputer fonts.
- enhancement: new "FF is Clear+Home" toggle (SyncTERM compatibility)
- bugfix: backspace with ATASCII

0.1.7 -- 2026-03-16
-------------------

- bugfix: strip DECSTBM (Set Scrolling Region) sequences from server when in linemode.
- bugfix: thousands of bbs's were erroneously set with utf8 encoding, set to cp437.
- bugfix: more some small TUI fixes/weaks
- enhancement: always display 'press Enter to return' prompt on disconnect.

0.1.6 -- 2026-03-15
-------------------

- bugfix vga colormatch for background colors, eg. xibalba bbs main menu.
- bugfix typescript file became 0 bytes when telnet linemode/raw switched.

0.1.5 -- 2026-03-14
-------------------

- enhancement: Add cp1252 encoding (Medievia)

0.1.4 -- 2026-03-13
-------------------

- bugfix: Make more effort to track rooms on servers like Medievia that do not serve any room id's,
  and, remove erroneously assigned "pk"

0.1.3 -- 2026-03-13
-------------------

- enhancement: support non-GMCP complaint room keys, ("vnum", "id", "pk") (Medievia)

0.1.2 -- 2026-03-13
-------------------

- bugfix: progress bar TUI was silently disappearing on edit.
- bugfix: cmd.exe failing to send any TERM type, now sends "ansi"
- bugfix: MTTS bitvector now declares 256-color support when truecolor
- enhancement: selecting type "Mud" now sends TERM=XTERM-TRUECOLOR by default

0.1.1 -- 2026-03-12
--------------------

- bugfix: GMCP package names by title-casing ``char.vitals`` -> ``Char.Vitals``,
  fixes room data and progress bars for Aardwolf (and probably others).

0.1.0 -- 2026-03-09
--------------------

- Initial public alpha release.
