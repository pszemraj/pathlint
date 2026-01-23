#!/usr/bin/env python3
"""Comprehensive test suite for pathlint."""

import ast
import sys
from pathlib import Path

import pytest

from pathlint.linter import OSPathDetector, find_python_files, lint_file, main


class TestOSPathDetector:
    """Test the enhanced AST visitor that detects ALL os.path patterns."""

    def test_import_os_path(self) -> None:
        """Test detection of 'import os.path'."""
        code = "import os.path\nprint('hello')"
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        assert len(detector.offenses) == 1
        assert 1 in detector.offenses
        assert "import os.path" in detector.offenses[1]

    def test_import_os_path_as_alias(self) -> None:
        """Test detection of 'import os.path as ospath'."""
        code = "import os.path as ospath\nprint(ospath.exists('test'))"
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        # Should detect both the import and the aliased usage
        assert 1 in detector.offenses
        assert "ospath" in detector.path_aliases

    def test_import_os_then_use_path(self) -> None:
        """Test detection of 'import os' followed by 'os.path.exists()'."""
        code = "import os\nresult = os.path.exists('test')"
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        assert detector.os_imported is True
        assert 2 in detector.offenses
        assert "os.path.exists('test')" in next(iter(detector.offenses[2]))

    def test_from_os_import_path(self) -> None:
        """Test detection of 'from os import path'."""
        code = "from os import path\nprint(path.join('a', 'b'))"
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        assert 1 in detector.offenses
        assert "path" in detector.path_aliases
        # Should also detect aliased usage
        assert 2 in detector.offenses

    def test_from_os_import_path_as_alias(self) -> None:
        """Test detection of 'from os import path as ospath'."""
        code = "from os import path as ospath\nresult = ospath.exists('file')"
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        assert 1 in detector.offenses
        assert "ospath" in detector.path_aliases
        # Should detect the aliased usage
        assert 2 in detector.offenses

    def test_from_os_path_import(self) -> None:
        """Test detection of 'from os.path import ...'."""
        code = "from os.path import join, exists\nresult = join('a', 'b')"
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        assert 1 in detector.offenses
        assert "from os.path import join, exists" in detector.offenses[1]

    def test_type_annotation_function_arg(self) -> None:
        """Test detection in function argument type annotations."""
        code = "import os\ndef process(path: os.path.PathLike):\n    pass"
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        assert 2 in detector.offenses
        assert "def process(path: os.path.PathLike):" in next(iter(detector.offenses[2]))

    def test_type_annotation_return(self) -> None:
        """Test detection in function return type annotations."""
        code = "import os\ndef get_path() -> os.path.PathLike:\n    return 'test'"
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        assert 2 in detector.offenses

    def test_type_annotation_variable(self) -> None:
        """Test detection in variable type annotations."""
        code = "import os\npath_var: os.path.PathLike = 'test'"
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        assert 2 in detector.offenses

    def test_no_duplicate_counting(self) -> None:
        """Test that multiple os.path on same line are deduplicated."""
        code = "import os\nx = os.path.join(os.path.dirname(__file__), 'test')"
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        # Line 2 should only appear once in offenses, even with multiple os.path
        assert 2 in detector.offenses
        assert len(detector.offenses[2]) == 1  # Set deduplicates

    def test_no_os_path_usage(self) -> None:
        """Test that pathlib usage is not flagged."""
        code = """from pathlib import Path
p = Path('test.txt')
print(p.exists())"""
        lines = code.splitlines()
        tree = ast.parse(code)
        detector = OSPathDetector(lines)
        detector.visit(tree)
        assert len(detector.offenses) == 0


class TestLintFile:
    """Test the enhanced lint_file function."""

    def test_early_termination_no_os_or_path(self, tmp_path: Path) -> None:
        """Test that files without 'os' or 'path' skip AST parsing."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""import sys
print('Hello World')
def main():
    return 42""")

        offenses = lint_file(test_file)
        assert len(offenses) == 0
        # File should be skipped early without AST parsing

    def test_lint_file_with_offenses(self, tmp_path: Path) -> None:
        """Test linting a file with os.path usage."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""import os.path
print(os.path.exists('test'))
""")

        offenses = lint_file(test_file)
        assert len(offenses) == 2
        assert offenses[0] == (1, "import os.path")
        assert offenses[1] == (2, "print(os.path.exists('test'))")

    def test_lint_file_with_syntax_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test linting a file with syntax errors.

        Args:
            tmp_path: Pytest fixture for temporary directory.
            capsys: Pytest fixture for capturing stdout/stderr.
        """
        test_file = tmp_path / "test.py"
        test_file.write_text("import os.path\nif True\n    print('syntax error')")

        offenses = lint_file(test_file)
        assert len(offenses) == 0

        captured = capsys.readouterr()
        assert "✗ Syntax error" in captured.err

    def test_lint_file_unicode_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test handling of unicode decode errors.

        Args:
            tmp_path: Pytest fixture for temporary directory.
            capsys: Pytest fixture for capturing stdout/stderr.
            monkeypatch: Pytest fixture for mocking.
        """
        test_file = tmp_path / "test.py"
        test_file.write_bytes(b"\xff\xfe")  # Invalid UTF-8

        offenses = lint_file(test_file)
        assert len(offenses) == 0

        captured = capsys.readouterr()
        assert "✗ Cannot read" in captured.err

    def test_lint_nonexistent_file(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test linting a non-existent file."""
        offenses = lint_file(Path("/nonexistent/file.py"))
        assert len(offenses) == 0

        captured = capsys.readouterr()
        assert "✗ Cannot read" in captured.err


class TestFindPythonFiles:
    """Test the enhanced file finding with smart exclusions."""

    def test_exclude_common_dirs(self, tmp_path: Path) -> None:
        """Test auto-exclusion of __pycache__, venv, node_modules."""
        # Create directory structure
        (tmp_path / "main.py").write_text("print('main')")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "cached.py").write_text("print('cached')")
        (tmp_path / "venv").mkdir()
        (tmp_path / "venv" / "venv_file.py").write_text("print('venv')")
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "venv_file2.py").write_text("print('.venv')")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "node.py").write_text("print('node')")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "good.py").write_text("print('good')")

        files = find_python_files([str(tmp_path)])
        file_names = {f.name for f in files}

        assert "main.py" in file_names
        assert "good.py" in file_names
        assert "cached.py" not in file_names
        assert "venv_file.py" not in file_names
        assert "venv_file2.py" not in file_names
        assert "node.py" not in file_names

    def test_custom_exclude_patterns(self, tmp_path: Path) -> None:
        """Test custom exclusion patterns."""
        (tmp_path / "main.py").write_text("print('main')")
        (tmp_path / "test").mkdir()
        (tmp_path / "test" / "test.py").write_text("print('test')")

        files = find_python_files([str(tmp_path)], exclude_patterns={"test"})
        file_names = {f.name for f in files}

        assert "main.py" in file_names
        assert "test.py" not in file_names

    def test_single_file_path(self, tmp_path: Path) -> None:
        """Test providing a single file path."""
        test_file = tmp_path / "single.py"
        test_file.write_text("print('single')")

        files = find_python_files([str(test_file)])
        assert len(files) == 1
        assert test_file in files

    def test_non_python_file(self, tmp_path: Path) -> None:
        """Test that non-Python files are excluded."""
        (tmp_path / "test.txt").write_text("not python")
        (tmp_path / "test.py").write_text("python")

        files = find_python_files([str(tmp_path)])
        file_names = {f.name for f in files}

        assert "test.py" in file_names
        assert "test.txt" not in file_names


class TestMainCLI:
    """Test the enhanced main CLI function."""

    def test_main_with_stats_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test --stats flag shows worst offenders.

        Args:
            tmp_path: Pytest fixture for temporary directory.
            capsys: Pytest fixture for capturing stdout/stderr.
            monkeypatch: Pytest fixture for mocking.
        """
        # Create files with different numbers of offenses
        (tmp_path / "worst.py").write_text("""import os.path
from os import path
x = os.path.join('a', 'b')
y = os.path.exists('test')
z = path.dirname(__file__)""")

        (tmp_path / "medium.py").write_text("""import os.path
x = os.path.exists('test')""")

        (tmp_path / "clean.py").write_text("from pathlib import Path")

        monkeypatch.setattr(sys, "argv", ["pathlint", str(tmp_path), "--stats"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Worst offenders:" in captured.out
        assert "worst.py" in captured.out

    def test_main_with_aggressive_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test --aggressive flag shows aggressive message.

        Args:
            tmp_path: Pytest fixture for temporary directory.
            capsys: Pytest fixture for capturing stdout/stderr.
            monkeypatch: Pytest fixture for mocking.
        """
        test_file = tmp_path / "test.py"
        test_file.write_text("import os.path")

        monkeypatch.setattr(sys, "argv", ["pathlint", str(test_file), "--aggressive"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "⚠️  ARE YOU KIDDING ME? USE PATHLIB! ⚠️" in captured.out

    def test_main_professional_output_format(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test professional output format.

        Args:
            tmp_path: Pytest fixture for temporary directory.
            capsys: Pytest fixture for capturing stdout/stderr.
            monkeypatch: Pytest fixture for mocking.
        """
        test_file = tmp_path / "test.py"
        test_file.write_text("""import os.path
x = os.path.join('a', 'b')
def f(p: os.path.PathLike):
    pass""")

        monkeypatch.setattr(sys, "argv", ["pathlint", str(test_file)])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()

        # Check professional format
        assert "L   1:" in captured.out or "L1:" in captured.out
        assert "────────────" in captured.out
        assert "Files checked:" in captured.out
        assert "Files with issues:" in captured.out
        assert "Total violations:" in captured.out
        assert "✗ Found os.path usage. Migrate to pathlib." in captured.out

    def test_main_clean_files(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test output when all files are clean.

        Args:
            tmp_path: Pytest fixture for temporary directory.
            capsys: Pytest fixture for capturing stdout/stderr.
            monkeypatch: Pytest fixture for mocking.
        """
        test_file = tmp_path / "test.py"
        test_file.write_text("from pathlib import Path\nprint(Path.cwd())")

        monkeypatch.setattr(sys, "argv", ["pathlint", str(test_file)])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "✓" in captured.out
        assert "no os.path usage found" in captured.out

    def test_main_no_files_found(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test when no Python files are found.

        Args:
            tmp_path: Pytest fixture for temporary directory.
            capsys: Pytest fixture for capturing stdout/stderr.
            monkeypatch: Pytest fixture for mocking.
        """
        # Create empty directory
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        monkeypatch.setattr(sys, "argv", ["pathlint", str(empty_dir)])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "No Python files found" in captured.out

    def test_main_nonexistent_path_error(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test error handling for non-existent paths.

        Args:
            capsys: Pytest fixture for capturing stdout/stderr.
            monkeypatch: Pytest fixture for mocking.
        """
        monkeypatch.setattr(sys, "argv", ["pathlint", "/nonexistent/path"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "✗ Path does not exist" in captured.err
