Session manager
===============

When launched without a host argument, Telix opens a Textual-based session manager.

This is a traditional "Dialing Directory" of host/port combinations and their settings
which may be set accordingly to preference of the remote system (BBS or MUD):

- Encoding (eg. utf-8, cp437, latin-1, gbk)
- Protocol: Telnet or WebSocket (``ws://`` / ``wss://``)
- SSL/TLS
- ICE colors (BBS)
- vga color matching (BBS)
- raw (BBS) or line mode (MUDs)
- Advanced REPL (MUDs)

To connect to a system, use the mouse to click the selected entry, or select
using keyboard and press return.

Once connected, disconnect using ``Control  + ]``, which returns to the session manager.

Server type presets
-------------------

The session editor provides BBS and MUD presets that configure sensible
defaults for each server type.  The same presets are available on the
command line via ``--bbs`` and ``--mud``.

``--bbs``
    Raw terminal mode, VGA color palette, iCE colors on, REPL disabled,
    MCCP compression passive.

``--mud``
    Line-buffered mode, color palette off, iCE colors off, REPL enabled,
    MCCP compression on.

Example::

    telix --bbs bbs.example.com
    telix --mud mud.example.com 4000
    telix --mud ws://mud.example.com:9119
