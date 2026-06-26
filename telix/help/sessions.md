## Session Manager

**Default key:** F1 (from within the session manager)

The session manager displays a searchable list of saved telnet and MUD
sessions.  Sessions are sorted by bookmark status, then most recently
connected, then name.

To connect to a system, use the mouse to click the selected entry, or select
using keyboard and press return.

Once connected, disconnect using ``Ctrl + ]``, which returns to the session manager.

### Buttons

| Button | Action |
|--------|--------|
| **Connect** | Connect to the selected session |
| **New** | Create a new session pre-filled with defaults |
| **Bookmark** | Toggle bookmark on the selected session |
| **Delete** | Delete the selected session |
| **Copy** | Copy the selected session |
| **Theme** | Change the TUI theme |
| **Edit** | Edit the selected session |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Enter** | Connect to the selected session |
| **N** | Create a new session |
| **E** | Edit the selected session |
| **B** | Toggle bookmark on the selected session |
| **D** | Delete the selected session |
| **C** | Copy the selected session |
| **T** | Change theme |
| **F1** | Open this help screen |
| **Q** | Quit Telix |

### Search

Type in the search field at the top to filter sessions by name, host,
port, or encoding.  The search matches case-insensitively.  Use the
arrow keys to move between the search field and the session table.

### Bookmarks

Bookmarked sessions are marked with **‡** and sorted to the top of the
list.  Use the Bookmark button or press **B** to toggle the bookmark on
the selected session.

### Flags

The Flags column shows short codes summarizing non-default session
options:

| Flag | Meaning |
|------|---------|
| **ws** | WebSocket connection |
| **ssl** | TLS/SSL connection |
| **raw** | Raw socket mode (no telnet negotiation) |
| **line** | Line mode |
| **ansi** | ANSI key mode |
| **eol** | ASCII line endings |
| **!ice** | iCE colors disabled |
| **!repl** | REPL disabled (display only) |
| **gfx** | Graphics font auto-detected |
| **ts** | Typescript session recording to a file |

### Session Editing

Press **E** or click Edit to open the session editor.  The editor
allows you to change all session options including host, port, encoding,
connection mode, triggers, macros, and more.  Press **N** or click
New to create a new session with default settings.


### Server type

The session editor has a **Server Type** radio (BBS / MUD) that applies
recommended defaults for the connection:

**BBS** sets Color Palette to vga, iCE Colors on, Raw mode, REPL off,
Clear Homes Cursor on, FF is Clear+Home on, Echo Mode to Remote, and
MCCP Compression to passive.  Most BBSs require an 80-column by 25-line
terminal.

**MUD** sets MCCP Compression on, Line mode, REPL on, Echo Mode to
Auto, Color Palette to none, and iCE Colors off.  Most MUDs expect a
screen width of 100 or 120 columns.

These are starting points -- all fields remain editable after selection.

### Encoding/Font

The Encoding/Font selector on the Terminal tab accepts both standard
Python encoding names (``utf8``, ``cp437``, ``latin-1``, etc.) and
SyncTERM bitmap font names:

| Font name | Wire encoding | SyncTERM ID |
|-----------|---------------|-------------|
| **topaz** | iso-8859-1 | 42 |
| **topaz-plus** | iso-8859-1 | 40 |
| **microknight** | iso-8859-1 | 41 |
| **microknight-plus** | iso-8859-1 | 39 |
| **p0t-noodle** | iso-8859-1 | 37 |
| **mosoul** | iso-8859-1 | 38 |

When a font name is selected and Kitty/Sixel rendering is enabled, telix
uses the font's wire encoding for the connection and renders using
that font's bitmap glyphs.

### Font Graphics

The **Font Graphics** radio on the Terminal tab selects the rendering
engine for bitmap fonts:

| Option | Description |
|--------|-------------|
| **None** | Standard terminal text (no bitmap rendering) |
| **Kitty/Sixel** | Render using terminal inline graphics protocol |

The Kitty/Sixel option requires a terminal that supports the Kitty
graphics protocol or sixel.

### Display options

The Display tab includes color and rendering options:

| Option | Description |
|--------|-------------|
| **Color Palette** | VGA, xterm, or none |
| **Brightness / Contrast** | Color adjustment percentages |
| **iCE Colors** | Use blink attribute as bright background |
| **Clear Homes Cursor** | Inject cursor-home before clear-screen (CTerm compatibility) |
| **FF is Clear+Home** | Treat Form Feed (0x0C) as clear screen and home cursor (SyncTERM compatibility) |
| **Columns / Rows** | Force virtual terminal size for graphics font (blank = auto) |
| **Echo Mode** | ``Auto`` (default -- follow telnet ECHO negotiation), ``Local`` (client echoes), ``Remote`` (server echoes) |

**Clear Homes Cursor** compensates for BBS software that sends
``ESC[2J`` (erase display) expecting the cursor to also return home,
which is a SyncTERM/CTerm behavior not present in standard VT100
terminals.  Enabled by default for BBS server type.

**FF is Clear+Home** compensates for BBS software that sends a Form Feed
(``\x0C``) character expecting the terminal to clear the screen and home
the cursor.  Standard VT100 terminals scroll the screen on Form Feed;
SyncTERM treats it as a clear-screen command.  Enabled by default for
BBS server type.
