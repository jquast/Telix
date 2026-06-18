"""Replace the CLI --help block in intro.rst between marker comments."""

import io
import re
import sys
from pathlib import Path

START_MARKER = ".. begin-cli-help"
END_MARKER = ".. end-cli-help"


def main() -> None:
    """Capture ``telix --help`` output and inject it into the RST file."""
    path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parent.parent / "docs" / "intro.rst"
    )

    buf = io.StringIO()
    # Import here so tox format step has telix available via usedevelop.
    from telix.main import build_help_parser  # noqa: PLC0415

    build_help_parser().print_help(file=buf)
    help_text = buf.getvalue().rstrip()

    lines = [".. code-block:: text", ""]
    for line in help_text.splitlines():
        lines.append(f"    {line}" if line else "")
    lines.append("")
    block = "\n".join(lines) + "\n"

    content = path.read_text(encoding="utf-8")

    pattern = re.compile(
        rf"({re.escape(START_MARKER)}\n).*?({re.escape(END_MARKER)})",
        re.DOTALL,
    )
    if not pattern.search(content):
        print(f"error: markers not found in {path}", file=sys.stderr)
        sys.exit(1)

    path.write_text(pattern.sub(rf"\1{block}\2", content), encoding="utf-8")
    print(f"updated CLI help block in {path}")


if __name__ == "__main__":
    main()
