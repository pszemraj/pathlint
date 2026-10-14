"""Shared subprocess setup for tests that run pathlint from temporary directories."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def source_env() -> dict[str, str]:
    """Prepare a fresh environment using this checkout and its coverage settings."""
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    if "COV_CORE_SOURCE" in env:
        # Child processes change cwd; keep the parent's branch-coverage settings.
        env["COV_CORE_CONFIG"] = str(root / "pyproject.toml")
    return env


@pytest.fixture
def invoke(source_env: dict[str, str]) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Return a module runner with optional output-encoding overrides."""

    def run(
        root: Path, *args: str, encoding: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = source_env.copy()
        if encoding is not None:
            env.update(PYTHONUTF8="0", PYTHONIOENCODING=f"{encoding}:strict")
        return subprocess.run(
            [sys.executable, "-m", "pathlint", *args],
            cwd=root,
            env=env,
            text=True,
            encoding=encoding,
            capture_output=True,
            check=False,
        )

    return run
