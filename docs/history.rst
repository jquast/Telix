History
=======

0.1.8 -- unreleased
-------------------

- bugfix: correct some kinds of VGA color filtering
- bugfix: session manager table not redrawn on terminal resize
- enhancement: Display tab -- green checkmark or red X for detected background color
- enhancement: Display tab -- tooltips for iCE Colors, Force Black BG, Clear Homes Cursor,
  Octant Metafonts, Columns, and Rows
- enhancement: Display tab -- Octant Metafonts labeled "(experimental)" on its own row
- enhancement: Display tab -- improved layout for Clear Homes Cursor and metafont settings
- enhancement: new "FF is Clear+Home" toggle -- treats Form Feed (0x0C) as clear screen
  and home cursor, a SyncTERM compatibility feature required by many BBS systems
- enhancement: BBS preset enables Clear Homes Cursor and FF is Clear+Home; MUD disables both
- enhancement: ``--ff-clears-screen`` CLI flag and ``--reinit`` sets BBS presets accordingly

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
