"""Validate Textual DEFAULT_CSS strings in all Python source files."""

import ast
import sys
from pathlib import Path

from textual.css.stylesheet import Stylesheet
from textual.css.errors import UnresolvedVariableError


def find_css_strings(source: str, filename: str) -> list[tuple[int, str]]:
    """Extract DEFAULT_CSS string values from a Python source."""
    results: list[tuple[int, str]] = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return results

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DEFAULT_CSS":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        results.append((node.lineno, node.value.value))
                    elif isinstance(node.value, ast.BinOp):
                        parts: list[str] = []
                        for sub in ast.walk(node.value):
                            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                                parts.append(sub.value)
                        if parts:
                            results.append((node.lineno, "".join(parts)))
    return results


def main() -> int:
    """Validate all DEFAULT_CSS strings under telix/."""
    root = Path(__file__).resolve().parent.parent / "telix"
    errors = 0
    for pyfile in sorted(root.rglob("*.py")):
        source = pyfile.read_text(encoding="utf-8")
        for lineno, css in find_css_strings(source, str(pyfile)):
            stylesheet = Stylesheet()
            stylesheet.add_source(css)
            try:
                stylesheet.parse()
            except UnresolvedVariableError:
                pass
            except Exception as exc:
                print(f"{pyfile}:{lineno}: CSS parse error: {exc}", file=sys.stderr)
                errors += 1
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
