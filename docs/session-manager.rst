Session manager
===============

When launched without a host argument, telix opens a Textual-based session
manager.  The left panel lists saved sessions; selecting one shows its
connection details on the right.

Each session stores per-host options: encoding (utf-8, cp437, latin-1, and
more), SSL/TLS with optional certificate verification, raw or line mode,
connection timeout, environment variables, telnet option negotiation, color
matching, background color, and ICE colors.

The session list is persisted to ``$XDG_CONFIG_HOME/telix/sessions.json``.
