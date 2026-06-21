"""Replace generated blocks in RST docs between marker comments."""

import io
import re
import sys
import pathlib
import importlib

CLI_HELP_START = ".. begin-cli-help"
CLI_HELP_END = ".. end-cli-help"

FILE_OVERVIEW_START = ".. begin-file-overview"
FILE_OVERVIEW_END = ".. end-file-overview"

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

MODULE_GLOBS = [
    "telix/*.py",
    "telix/fonts/*.py",
]


def module_doc_first_line(module_name: str, source_path: pathlib.Path) -> str:
    """Return the first line of *module_name*'s docstring, or a placeholder."""
    try:
        mod = importlib.import_module(module_name)
        doc = mod.__doc__
    except Exception:
        # Fall back to AST when import fails (e.g. platform-specific deps).
        import ast

        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            doc = ast.get_docstring(tree)
        except Exception:
            doc = None
    if not doc:
        return "(no docstring)"
    return doc.strip().splitlines()[0]


def _overview_lines() -> list[str]:
    """Build the file overview block as a list of lines (no trailing newline)."""
    modules: list[tuple[str, str]] = []
    for pattern in MODULE_GLOBS:
        for py_file in sorted(PROJECT_ROOT.glob(pattern)):
            if py_file.name == "__init__.py" and py_file.parent.name == "fonts":
                continue
            rel = py_file.relative_to(PROJECT_ROOT / "telix")
            import_name = str(rel.with_suffix("")).replace("/", ".").replace("\\", ".")
            fq_name = f"telix.{import_name}"
            first_line = module_doc_first_line(fq_name, py_file)
            display = str(rel)
            modules.append((display, first_line))

    modules.sort(key=lambda m: m[0])

    max_name = max(len(name) for name, _ in modules)
    lines = [".. code-block:: text", ""]
    for name, doc in modules:
        lines.append(f"    {name:<{max_name}}  {doc}")
    lines.append("")
    return lines


def _update_block(
    path: pathlib.Path,
    start_marker: str,
    end_marker: str,
    lines: list[str],
    label: str,
) -> None:
    """Replace the block between *start* and *end* markers in *path*."""
    block = "\n".join(lines) + "\n"
    content = path.read_text(encoding="utf-8")

    pattern = re.compile(
        rf"({re.escape(start_marker)}\n).*?({re.escape(end_marker)})",
        re.DOTALL,
    )
    if not pattern.search(content):
        print(f"error: {label} markers not found in {path}", file=sys.stderr)
        sys.exit(1)

    path.write_text(pattern.sub(rf"\1{block}\2", content), encoding="utf-8")
    print(f"updated {label} in {path}")


def main() -> None:
    """Update all generated doc blocks."""
    # CLI help block in intro.rst
    intro_path = PROJECT_ROOT / "docs" / "intro.rst"
    buf = io.StringIO()
    from telix.main import build_help_parser

    build_help_parser().print_help(file=buf)
    help_text = buf.getvalue().rstrip()

    help_lines = [".. code-block:: text", ""]
    for line in help_text.splitlines():
        help_lines.append(f"    {line}" if line else "")
    help_lines.append("")
    _update_block(intro_path, CLI_HELP_START, CLI_HELP_END, help_lines, "CLI help")

    # File overview block in contributing.rst
    contrib_path = PROJECT_ROOT / "docs" / "contributing.rst"
    _update_block(
        contrib_path,
        FILE_OVERVIEW_START,
        FILE_OVERVIEW_END,
        _overview_lines(),
        "file overview",
    )


if __name__ == "__main__":
    main()
