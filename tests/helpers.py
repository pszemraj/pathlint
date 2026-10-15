"""Analyze and execute Python source fixtures."""

import ast
from textwrap import dedent

from pathlint.linter import OSPathDetector


def offenses(source: str) -> set[int]:
    """Return offending lines in a dedented, stripped source snippet."""
    detector = OSPathDetector()
    detector.visit(ast.parse(dedent(source).strip()))
    return detector.offenses


def execute(source: str) -> dict[str, object]:
    """Execute source in a fresh namespace without inheriting future flags."""
    namespace: dict[str, object] = {}
    exec(compile(source, "<fixture>", "exec", dont_inherit=True), namespace)
    return namespace


def run(source: str) -> object:
    """Execute a fixture and return its result."""
    return execute(source)["result"]
