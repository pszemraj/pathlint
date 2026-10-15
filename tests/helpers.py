"""Analyze and execute Python source fixtures."""

import ast
import subprocess
import sys
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


def run_isolated(source: str, program: str) -> str:
    """Return stdout from a fixture runner that restores its mutated module state.

    :param str source: Fixture source passed as the runner's first argument.
    :param str program: Runner responsible for execution and restoration before process exit.
    :return str: Captured output after successful execution without stderr diagnostics.
    """
    result = subprocess.run(
        [sys.executable, "-c", program, source], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout
