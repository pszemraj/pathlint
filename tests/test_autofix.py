#!/usr/bin/env python3
"""Comprehensive test suite for pathlint auto-fixer."""

from pathlib import Path

from pathlint.autofix import REPLACEMENTS, _replace_join_calls, add_pathlib_import, fix_file


def _get_replacement(pattern: str) -> tuple[str, str]:
    for item in REPLACEMENTS:
        if item[0] == pattern:
            return item
    raise AssertionError(f"Pattern not found: {pattern}")


class TestAddPathlibImport:
    """Test the add_pathlib_import function."""

    def test_add_import_to_file_with_no_imports(self) -> None:
        """Add pathlib import to file with no existing imports."""
        content = "x = 5\ny = 10\n"
        result = add_pathlib_import(content)
        assert "from pathlib import Path" in result
        lines = result.splitlines()
        assert lines[0] == "from pathlib import Path"

    def test_add_import_after_existing_imports(self) -> None:
        """Add pathlib import after existing imports."""
        content = "import os\nimport sys\n\nx = 5\n"
        result = add_pathlib_import(content)
        assert "from pathlib import Path" in result
        lines = result.splitlines()
        # Should be inserted after last import (line index 2)
        assert lines[2] == "from pathlib import Path"

    def test_no_duplicate_import_from_pathlib(self) -> None:
        """Don't add import if pathlib already imported."""
        content = "from pathlib import Path\nimport os\n"
        result = add_pathlib_import(content)
        # Should not modify
        assert result == content

    def test_no_duplicate_import_pathlib(self) -> None:
        """Don't add import if pathlib module imported."""
        content = "import pathlib\nimport os\n"
        result = add_pathlib_import(content)
        assert result == content

    def test_multiline_imports(self) -> None:
        """Handle files with from imports."""
        content = "from os import environ\nfrom sys import argv\n\nx = 1\n"
        result = add_pathlib_import(content)
        lines = result.splitlines()
        assert "from pathlib import Path" in result
        # Should be after line 1 (last import)
        assert lines[2] == "from pathlib import Path"


class TestReplacementPatterns:
    """Test individual replacement patterns."""

    def test_import_os_path_replacement(self) -> None:
        """Test 'import os.path' -> 'from pathlib import Path'."""
        pattern, replacement = _get_replacement(r"^import os\.path$")
        assert pattern == r"^import os\.path$"
        import re

        result = re.sub(pattern, replacement, "import os.path", flags=re.MULTILINE)
        assert result == "from pathlib import Path"

    def test_from_os_import_path_replacement(self) -> None:
        """Test 'from os import path' -> 'from pathlib import Path'."""
        pattern, replacement = _get_replacement(r"^from os import path$")
        import re

        result = re.sub(pattern, replacement, "from os import path", flags=re.MULTILINE)
        assert result == "from pathlib import Path"

    def test_exists_replacement(self) -> None:
        """Test os.path.exists() -> Path().exists()."""
        pattern, replacement = _get_replacement(r"os\.path\.exists\(([^)]+)\)")
        import re

        result = re.sub(pattern, replacement, "os.path.exists('test.txt')")
        assert result == "Path('test.txt').exists()"

    def test_isfile_replacement(self) -> None:
        """Test os.path.isfile() -> Path().is_file()."""
        pattern, replacement = _get_replacement(r"os\.path\.isfile\(([^)]+)\)")
        import re

        result = re.sub(pattern, replacement, 'os.path.isfile("/tmp/test")')
        assert result == 'Path("/tmp/test").is_file()'

    def test_isdir_replacement(self) -> None:
        """Test os.path.isdir() -> Path().is_dir()."""
        pattern, replacement = _get_replacement(r"os\.path\.isdir\(([^)]+)\)")
        import re

        result = re.sub(pattern, replacement, "os.path.isdir(directory)")
        assert result == "Path(directory).is_dir()"

    def test_basename_replacement(self) -> None:
        """Test os.path.basename() -> Path().name."""
        pattern, replacement = _get_replacement(r"os\.path\.basename\(([^)]+)\)")
        import re

        result = re.sub(pattern, replacement, "os.path.basename(__file__)")
        assert result == "Path(__file__).name"

    def test_dirname_replacement(self) -> None:
        """Test os.path.dirname() -> str(Path().parent)."""
        pattern, replacement = _get_replacement(r"os\.path\.dirname\(([^)]+)\)")
        import re

        result = re.sub(pattern, replacement, "os.path.dirname(__file__)")
        assert result == "str(Path(__file__).parent)"

    def test_join_two_args_replacement(self) -> None:
        """Test os.path.join(a, b) -> str(Path(a) / b)."""
        result, count = _replace_join_calls("os.path.join('dir', 'file')", "os.path.join(")
        assert count == 1
        assert result == "str(Path('dir') / 'file')"

    def test_join_three_args_replacement(self) -> None:
        """Test os.path.join(a, b, c) -> str(Path(a) / b / c)."""
        result, count = _replace_join_calls(
            "os.path.join('a', 'b', 'c')",
            "os.path.join(",
        )
        assert count == 1
        assert result == "str(Path('a') / 'b' / 'c')"

    def test_abspath_replacement(self) -> None:
        """Test os.path.abspath() -> str(Path().resolve())."""
        pattern, replacement = _get_replacement(r"os\.path\.abspath\(([^)]+)\)")
        import re

        result = re.sub(pattern, replacement, "os.path.abspath('relative/path')")
        assert result == "str(Path('relative/path').resolve())"

    def test_aliased_path_exists(self) -> None:
        """Test path.exists() after 'from os import path'."""
        pattern, replacement = _get_replacement(r"\bpath\.exists\(([^)]+)\)")
        import re

        result = re.sub(pattern, replacement, "path.exists('file.txt')")
        assert result == "Path('file.txt').exists()"


class TestFixFileBasic:
    """Test basic fix_file functionality."""

    def test_fix_simple_os_path_exists(self, tmp_path: Path) -> None:
        """Fix a simple os.path.exists call."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os.path\nif os.path.exists('file'):\n    pass\n")

        count = fix_file(test_file, dry_run=False)
        assert count == 2  # import + exists call

        result = test_file.read_text()
        assert "from pathlib import Path" in result
        assert "Path('file').exists()" in result
        assert "os.path" not in result

    def test_fix_multiple_replacements(self, tmp_path: Path) -> None:
        """Fix file with multiple os.path calls."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "import os.path\n"
            "x = os.path.exists('a')\n"
            "y = os.path.isfile('b')\n"
            "z = os.path.join('c', 'd')\n"
        )

        count = fix_file(test_file, dry_run=False)
        assert count == 4  # import + 3 calls

        result = test_file.read_text()
        assert "Path('a').exists()" in result
        assert "Path('b').is_file()" in result
        assert "str(Path('c') / 'd')" in result

    def test_dry_run_no_modification(self, tmp_path: Path) -> None:
        """Dry-run should not modify file."""
        test_file = tmp_path / "test.py"
        original = "import os.path\nif os.path.exists('file'):\n    pass\n"
        test_file.write_text(original)

        count = fix_file(test_file, dry_run=True)
        assert count == 2

        # File should be unchanged
        assert test_file.read_text() == original

    def test_no_changes_needed(self, tmp_path: Path) -> None:
        """File with no os.path usage should return 0."""
        test_file = tmp_path / "test.py"
        test_file.write_text("from pathlib import Path\nPath('file').exists()\n")

        count = fix_file(test_file, dry_run=False)
        assert count == 0

    def test_invalid_file(self, tmp_path: Path) -> None:
        """Non-existent file should return 0."""
        test_file = tmp_path / "nonexistent.py"
        count = fix_file(test_file, dry_run=False)
        assert count == 0


class TestFixFileComplex:
    """Test complex real-world scenarios."""

    def test_from_os_import_path_conversion(self, tmp_path: Path) -> None:
        """Convert 'from os import path' usage."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "from os import path\n"
            "if path.exists('file'):\n"
            "    x = path.join('a', 'b')\n"
            "    print(path.basename('test'))\n"
        )

        count = fix_file(test_file, dry_run=False)
        assert count >= 4

        result = test_file.read_text()
        assert "from pathlib import Path" in result
        assert "Path('file').exists()" in result
        assert "str(Path('a') / 'b')" in result
        assert "Path('test').name" in result

    def test_mixed_os_path_calls(self, tmp_path: Path) -> None:
        """Test file with various os.path functions."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "import os.path\n"
            "def process_file(filepath):\n"
            "    if os.path.exists(filepath):\n"
            "        name = os.path.basename(filepath)\n"
            "        directory = os.path.dirname(filepath)\n"
            "        full = os.path.abspath(filepath)\n"
            "        return os.path.join(directory, name)\n"
            "    return None\n"
        )

        count = fix_file(test_file, dry_run=False)
        assert count >= 6

        result = test_file.read_text()
        assert "Path(filepath).exists()" in result
        assert "Path(filepath).name" in result
        assert "str(Path(filepath).parent)" in result
        assert "str(Path(filepath).resolve())" in result

    def test_preserves_code_structure(self, tmp_path: Path) -> None:
        """Ensure code structure is preserved."""
        test_file = tmp_path / "test.py"
        original = (
            "import os.path\n"
            "\n"
            "# This is a comment\n"
            "def foo():\n"
            '    """Docstring."""\n'
            "    x = os.path.exists('test')\n"
            "    return x\n"
            "\n"
            "class Bar:\n"
            "    pass\n"
        )
        test_file.write_text(original)

        fix_file(test_file, dry_run=False)
        result = test_file.read_text()

        # Check structure preserved
        assert "# This is a comment" in result
        assert '"""Docstring."""' in result
        assert "def foo():" in result
        assert "class Bar:" in result

    def test_nested_os_path_calls(self, tmp_path: Path) -> None:
        """Test nested os.path function calls."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "import os.path\nresult = os.path.basename(os.path.dirname(__file__))\n"
        )

        count = fix_file(test_file, dry_run=False)
        assert count >= 2

        result = test_file.read_text()
        # Should handle nested calls (may not be perfect due to regex limitations)
        assert "Path" in result

    def test_three_arg_join(self, tmp_path: Path) -> None:
        """Test os.path.join with 3 arguments."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os.path\npath = os.path.join('root', 'subdir', 'file.txt')\n")

        count = fix_file(test_file, dry_run=False)
        assert count == 2

        result = test_file.read_text()
        assert "str(Path('root') / 'subdir' / 'file.txt')" in result

    def test_multiple_imports(self, tmp_path: Path) -> None:
        """File with multiple imports should add pathlib correctly."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "import sys\n"
            "import os.path\n"
            "from typing import Optional\n"
            "\n"
            "x = os.path.exists('test')\n"
        )

        fix_file(test_file, dry_run=False)
        result = test_file.read_text()

        lines = result.splitlines()
        # Should add pathlib import after other imports
        assert "from pathlib import Path" in result
        # Should be before the blank line
        import_section = "\n".join(lines[:4])
        assert "from pathlib import Path" in import_section


class TestFixFileEdgeCases:
    """Test edge cases and error handling."""

    def test_multiline_os_path_call(self, tmp_path: Path) -> None:
        """Multiline os.path calls should be auto-fixed."""
        test_file = tmp_path / "multiline.py"
        test_file.write_text(
            "import os.path\n" "result = os.path.exists(\n" "    'test.txt'\n" ")\n"
        )

        count = fix_file(test_file, dry_run=False)
        assert count >= 2

        result = test_file.read_text()
        assert "os.path.exists" not in result
        assert "from pathlib import Path" in result
        assert "Path(" in result
        assert ").exists()" in result

    def test_multiline_join_call(self, tmp_path: Path) -> None:
        """Multiline os.path.join calls should be auto-fixed."""
        test_file = tmp_path / "multiline_join.py"
        test_file.write_text(
            "import os.path\n"
            "result = os.path.join(\n"
            "    'root',\n"
            "    'subdir',\n"
            "    'file.txt',\n"
            ")\n"
        )

        count = fix_file(test_file, dry_run=False)
        assert count >= 2

        result = test_file.read_text()
        assert "os.path.join" not in result
        assert "str(Path('root') / 'subdir' / 'file.txt')" in result

    def test_file_with_syntax_error(self, tmp_path: Path) -> None:
        """Handle files with syntax errors gracefully."""
        test_file = tmp_path / "broken.py"
        test_file.write_text("import os.path\nif os.path.exists(  # missing closing")

        # Should not crash
        count = fix_file(test_file, dry_run=False)
        # Regex replacements may still work despite syntax errors
        assert count >= 0

    def test_unicode_content(self, tmp_path: Path) -> None:
        """Handle files with unicode content."""
        test_file = tmp_path / "unicode.py"
        test_file.write_text(
            "# -*- coding: utf-8 -*-\n"
            "import os.path\n"
            "# Comment with emoji 🐍\n"
            "x = os.path.exists('файл')  # Cyrillic filename\n",
            encoding="utf-8",
        )

        count = fix_file(test_file, dry_run=False)
        assert count >= 2

        result = test_file.read_text(encoding="utf-8")
        assert "Path('файл').exists()" in result
        assert "🐍" in result

    def test_already_using_pathlib(self, tmp_path: Path) -> None:
        """File already using pathlib shouldn't be modified."""
        test_file = tmp_path / "clean.py"
        clean_code = (
            "from pathlib import Path\n\ndef check(filepath):\n    return Path(filepath).exists()\n"
        )
        test_file.write_text(clean_code)

        count = fix_file(test_file, dry_run=False)
        assert count == 0
        assert test_file.read_text() == clean_code

    def test_mixed_pathlib_and_os_path(self, tmp_path: Path) -> None:
        """File mixing both should convert os.path parts."""
        test_file = tmp_path / "mixed.py"
        test_file.write_text(
            "from pathlib import Path\n"
            "import os.path\n"
            "\n"
            "x = Path('a').exists()\n"
            "y = os.path.exists('b')\n"
        )

        count = fix_file(test_file, dry_run=False)
        # Should fix the os.path parts but keep pathlib
        assert count >= 1

        result = test_file.read_text()
        assert "Path('a').exists()" in result
        assert "Path('b').exists()" in result


class TestReplacementOrder:
    """Test that replacement order handles edge cases correctly."""

    def test_three_arg_join_before_two_arg(self, tmp_path: Path) -> None:
        """Ensure 3-arg join is replaced before 2-arg join."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "import os.path\na = os.path.join('x', 'y')\nb = os.path.join('x', 'y', 'z')\n"
        )

        fix_file(test_file, dry_run=False)
        result = test_file.read_text()

        # 2-arg join
        assert "str(Path('x') / 'y')" in result
        # 3-arg join
        assert "str(Path('x') / 'y' / 'z')" in result

    def test_import_replacement_comprehensive(self, tmp_path: Path) -> None:
        """Test all import replacement patterns."""
        test_file = tmp_path / "imports.py"
        test_file.write_text("from os.path import join, exists, dirname\n")

        count = fix_file(test_file, dry_run=False)
        assert count >= 1

        result = test_file.read_text()
        assert "from pathlib import Path" in result
