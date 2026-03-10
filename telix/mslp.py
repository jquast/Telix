r"""
MSLP (Mud Server Link Protocol) keyboard navigation.

Parses OSC 68 sequences from server output, strips them from display,
and collects SEND-type commands for TAB/SHIFT+TAB cycling on the input row.

Wire format::

    ESC ] 68 ; TYPE ; LABEL ; COMMAND (BEL | ESC \\)

Type 1 = SEND link (collected), other types are stripped but ignored.
"""

import re
import dataclasses

OSC68_RE = re.compile(r"\x1b\]68;(\d+);([^;]*);([^\x07\x1b]*)(?:\x07|\x1b\\)")


@dataclasses.dataclass
class MslpLink:
    """A single MSLP link: command text and display label."""

    command: str
    label: str


class MslpCollector:
    """Collect MSLP links from server output, scoped by EOR/GA rounds."""

    def __init__(self) -> None:
        self.pending: list[MslpLink] = []
        self.available: list[MslpLink] = []

    def filter(self, text: str) -> str:
        """
        Strip OSC 68 sequences from *text* and collect type-1 commands.

        :param text: Raw server output potentially containing OSC 68 sequences.
        :returns: Cleaned text with OSC 68 sequences removed.
        """

        def replacer(m: re.Match[str]) -> str:
            link_type = int(m.group(1))
            if link_type == 1:
                label = m.group(2)
                command = m.group(3)
                self.pending.append(MslpLink(command=command, label=label))
            return ""

        return OSC68_RE.sub(replacer, text)

    def on_prompt(self) -> None:
        """EOR/GA received: promote pending to available, reset pending."""
        if self.pending:
            self.available = self.pending
        self.pending = []

    @property
    def count(self) -> int:
        """Number of available commands for cycling."""
        return len(self.available)
