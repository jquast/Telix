"""Replace the CLI --help block in intro.rst between marker comments."""

import re
import subprocess
import sys
from pathlib import Path

START_MARKER = ".. begin-cli-help"
END_MARKER = ".. end-cli-help"


def main() -> None:
    """Run ``telix --help`` and inject the output into intro.rst."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "intro.rst"

    result = subprocess.run(["telix", "--help"], capture_output=True, text=True, check=True)

    lines = [".. code-block:: text", ""]
    for line in result.stdout.rstrip().splitlines():
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
