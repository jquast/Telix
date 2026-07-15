"""Validate all DEFAULT_CSS strings parses without syntax errors."""

import ast

import pytest
from textual.css.errors import UnresolvedVariableError
from textual.css.stylesheet import Stylesheet


def find_css_strings(source: str, filename: str) -> list[tuple[int, str]]:
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


def collect_css_targets():
    import telix

    root = telix.__path__[0]
    from pathlib import Path

    targets: list[tuple[str, int, str]] = []
    for pyfile in sorted(Path(root).rglob("*.py")):
        source = pyfile.read_text(encoding="utf-8")
        for lineno, css in find_css_strings(source, str(pyfile)):
            targets.append((pyfile.name, lineno, css))
    return targets


@pytest.mark.parametrize("filename,lineno,css", collect_css_targets())
def test_default_css_parses(filename, lineno, css):
    stylesheet = Stylesheet()
    stylesheet.add_source(css)
    try:
        stylesheet.parse()
    except UnresolvedVariableError:
        pass
