Protocols
---------

Telix supports Telnet, Websockets, and SSH.

Telnet
~~~~~~

Telix supports the following relevant RFCs:

* :rfc:`854` Telnet Protocol Specification
* :rfc:`855` Telnet Option Specifications
* :rfc:`856` Telnet Binary Transmission (BINARY)
* :rfc:`857` Telnet Echo Option (ECHO)
* :rfc:`858` Telnet Suppress Go Ahead Option (SGA)
* :rfc:`885` Telnet End of Record Option (EOR)
* :rfc:`1073` Telnet Window Size Option (NAWS)
* :rfc:`1079` Telnet Terminal Speed Option (TSPEED)
* :rfc:`1091` Telnet Terminal-Type Option (TTYPE)
* :rfc:`1408` Telnet Environment Option (ENVIRON)
* :rfc:`1572` Telnet Environment Option (NEW_ENVIRON)
* :rfc:`2066` Telnet Charset Option (CHARSET)

Mud
~~~

The following MUD standards are supported:

* `MTTS`_ -- Mud Terminal Type Standard.  Client capability bitvector
  advertised via TTYPE cycling.
* `MNES`_ -- Mud New Environment Standard.  Structured client/server
  variable exchange over NEW-ENVIRON.
* `GMCP`_ -- Generic MUD Communication Protocol.  JSON-based
  bidirectional messaging for game data.
* `MSDP`_ -- MUD Server Data Protocol.  Structured key-value protocol
  for game variables.
* `MSSP`_ -- MUD Server Status Protocol.  Server metadata for MUD
  crawlers and directories.
* `MCCP`_ -- MUD Client Compression Protocol (v2 and v3).  Zlib
  compression for server-to-client and client-to-server data.
* `EOR`_ -- End of Record.  Marks the end of a prompt so the client
  can distinguish prompts from regular output.

.. _MTTS: https://tintin.mudhalla.net/protocols/mtts/
.. _MNES: https://tintin.mudhalla.net/protocols/mnes/
.. _GMCP: https://tintin.mudhalla.net/protocols/gmcp/
.. _MSDP: https://tintin.mudhalla.net/protocols/msdp/
.. _MSSP: https://tintin.mudhalla.net/protocols/mssp/
.. _MCCP: https://tintin.mudhalla.net/protocols/mccp/
.. _EOR: https://tintin.mudhalla.net/protocols/eor/

WebSocket
~~~~~~~~~

Connections use ``ws://`` or ``wss://`` (TLS) URLs.  Telix advertises all three
`mudstandards.org WebSocket subprotocols`_ during the opening handshake and selects
its engine based on whichever the server accepts:

* ``telnet.mudstandards.org`` -- WebSocket binary frames carry a complete telnet
  stream including IAC option negotiation.  A full telnetlib3 client is used,
  giving access to all telnet options (NAWS, TTYPE, GMCP, MCCP, etc.) exactly as
  in a direct Telnet connection.
* ``gmcp.mudstandards.org`` -- binary frames carry UTF-8 game output and ANSI
  control codes; GMCP commands arrive as JSON text frames.  ECHO negotiation via
  sparse IAC sequences is handled transparently.
* ``terminal.mudstandards.org`` -- binary frames only; UTF-8 I/O and ANSI control
  codes with no telnet negotiation and no GMCP text frames.

If the server does not negotiate a recognised subprotocol, or if a bare IAC byte
(``0xFF``) appears in the first received frame on the GMCP or terminal path, Telix
automatically promotes the connection to the full telnet engine.

The fourth mudstandards.org subprotocol, ``json.mudstandards.org``, is not currently
supported.

.. _mudstandards.org WebSocket subprotocols: https://mudstandards.org/websocket/

SSH
~~~

Telix uses ``asyncssh`` for SSHv2 connections.  SSH connections run in
BBS-style raw mode -- there is no telnet negotiation.  Password and
keyboard-interactive authentication are supported.  Use the ``telix-ssh``
command to connect::

    telix-ssh bbs.example.com

SSH is suitable for BBS systems that offer SSH alongside Telnet.

