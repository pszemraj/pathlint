import ast
import sys
from pathlib import Path
from unittest import mock

import pytest

from pathlint.linter import OSPathVisitor, lint_file, main


class TestOSPathVisitor:
    """Test the AST visitor that detects os.path usage."""

    def test_import_os_path(self):
        """Test detection of 'import os.path'."""
        code = "import os.path\nprint('hello')"
        lines = code.splitlines()
        tree = ast.parse(code)
        visitor = OSPathVisitor(lines)
        visitor.visit(tree)
        assert len(visitor.found_os_path) == 1
        assert visitor.found_os_path[0] == (1, "import os.path")

    def test_from_os_import_path(self):
        """Test detection of 'from os import path'."""
        code = "from os import path\nprint(path.join('a', 'b'))"
        lines = code.splitlines()
        tree = ast.parse(code)
        visitor = OSPathVisitor(lines)
        visitor.visit(tree)
        assert len(visitor.found_os_path) == 1
        assert visitor.found_os_path[0] == (1, "from os import path")
    
    def test_from_os_path_import(self):
        """Test detection of 'from os.path import ...'."""
        code = "from os.path import join, exists\nresult = join('a', 'b')"
        lines = code.splitlines()
        tree = ast.parse(code)
        visitor = OSPathVisitor(lines)
        visitor.visit(tree)
        assert len(visitor.found_os_path) == 1
        assert visitor.found_os_path[0] == (1, "from os.path import join, exists")

    def test_os_path_attribute_access(self):
        """Test detection of os.path attribute access."""
        code = "import os\nsep = os.path.sep"
        lines = code.splitlines()
        tree = ast.parse(code)
        visitor = OSPathVisitor(lines)
        visitor.visit(tree)
        assert len(visitor.found_os_path) == 1
        assert visitor.found_os_path[0] == (2, "sep = os.path.sep")

    def test_os_path_function_call(self):
        """Test detection of os.path function calls."""
        code = "import os\nresult = os.path.join('dir', 'file.txt')"
        lines = code.splitlines()
        tree = ast.parse(code)
        visitor = OSPathVisitor(lines)
        visitor.visit(tree)
        # Note: This catches both the attribute access and the call
        assert len(visitor.found_os_path) == 2
        assert all(offense[0] == 2 for offense in visitor.found_os_path)

    def test_multiple_os_path_usages(self):
        """Test detection of multiple os.path usages."""
        code = """import os.path
from os import path
print(os.path.exists('test'))
x = os.path.sep"""
        lines = code.splitlines()
        tree = ast.parse(code)
        visitor = OSPathVisitor(lines)
        visitor.visit(tree)
        # Note: os.path.exists gets caught twice (attribute + call)
        assert len(visitor.found_os_path) == 5

    def test_no_os_path_usage(self):
        """Test that pathlib usage is not flagged."""
        code = """from pathlib import Path
p = Path('test.txt')
print(p.exists())"""
        lines = code.splitlines()
        tree = ast.parse(code)
        visitor = OSPathVisitor(lines)
        visitor.visit(tree)
        assert len(visitor.found_os_path) == 0

    def test_line_out_of_bounds(self):
        """Test handling when line number is out of bounds."""
        # Create a mock node with invalid line number
        lines = ["import os"]
        visitor = OSPathVisitor(lines)

        mock_node = mock.Mock()
        mock_node.lineno = 999  # Out of bounds

        visitor._add_offense(mock_node)
        assert len(visitor.found_os_path) == 1
        assert visitor.found_os_path[0][1] == "<source line not available>"


class TestLintFile:
    """Test the lint_file function."""

    def test_lint_file_with_offenses(self, tmp_path):
        """Test linting a file with os.path usage."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""import os.path
print(os.path.exists('test'))
""")

        offenses = lint_file(test_file)
        assert len(offenses) == 2
        assert offenses[0] == (1, "import os.path")
        assert offenses[1] == (2, "print(os.path.exists('test'))")

    def test_lint_file_without_offenses(self, tmp_path):
        """Test linting a file without os.path usage."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""from pathlib import Path
print(Path('test').exists())
""")

        offenses = lint_file(test_file)
        assert len(offenses) == 0

    def test_lint_file_with_syntax_error(self, tmp_path, capsys):
        """Test linting a file with syntax errors."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os.path\nif True\n    print('syntax error')")

        offenses = lint_file(test_file)
        assert len(offenses) == 0

        captured = capsys.readouterr()
        assert "SyntaxError" in captured.err

    def test_lint_nonexistent_file(self, capsys):
        """Test linting a non-existent file."""
        offenses = lint_file(Path("/nonexistent/file.py"))
        assert len(offenses) == 0

        captured = capsys.readouterr()
        assert "File not found" in captured.err

    def test_lint_file_removes_duplicates(self, tmp_path):
        """Test that duplicate offenses on the same line are deduplicated."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""import os.path
# Multiple os.path on same line
x = os.path.join(os.path.dirname(__file__), 'test')
""")

        offenses = lint_file(test_file)
        # Should have 2 offenses: line 1 and line 3 (deduplicated)
        assert len(offenses) == 2
        assert any(line_no == 1 for line_no, _ in offenses)
        assert any(line_no == 3 for line_no, _ in offenses)


class TestMain:
    """Test the main CLI function."""

    def test_main_single_file_with_offenses(self, tmp_path, capsys, monkeypatch):
        """Test main function with a single file containing offenses."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os.path\n")

        monkeypatch.setattr(sys, 'argv', ['pathlint', str(test_file)])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Offenses found in:" in captured.out
        assert "ARE YOU DUMB" in captured.out
        assert "Found a total of 1 'os.path' instance(s)" in captured.out

    def test_main_single_file_no_offenses(self, tmp_path, capsys, monkeypatch):
        """Test main function with a clean file."""
        test_file = tmp_path / "test.py"
        test_file.write_text("from pathlib import Path\n")

        monkeypatch.setattr(sys, 'argv', ['pathlint', str(test_file)])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Congratulations! No 'os.path' usage found" in captured.out

    def test_main_directory(self, tmp_path, capsys, monkeypatch):
        """Test main function with a directory."""
        # Create multiple Python files
        (tmp_path / "file1.py").write_text("import os.path")
        (tmp_path / "file2.py").write_text("from pathlib import Path")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "file3.py").write_text("print(os.path.sep)")

        monkeypatch.setattr(sys, 'argv', ['pathlint', str(tmp_path)])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Processed 3 Python file(s)" in captured.out
        assert "Found a total of 2 'os.path' instance(s) in 2 file(s)" in captured.out

    def test_main_silent_mode(self, tmp_path, capsys, monkeypatch):
        """Test main function with --silent flag."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os.path\n")

        monkeypatch.setattr(sys, 'argv', ['pathlint', str(test_file), '--silent'])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ARE YOU DUMB" not in captured.out
        assert "Offenses found in:" in captured.out

    def test_main_nonexistent_path(self, capsys, monkeypatch):
        """Test main function with non-existent path."""
        monkeypatch.setattr(sys, 'argv', ['pathlint', '/nonexistent/path.py'])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "does not exist" in captured.err
        assert "No Python files found" in captured.out

    def test_main_non_python_file(self, tmp_path, capsys, monkeypatch):
        """Test main function with non-Python file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Not a Python file")

        monkeypatch.setattr(sys, 'argv', ['pathlint', str(test_file)])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Skipping non-Python file" in captured.err
        assert "No Python files found" in captured.out

    def test_main_multiple_paths(self, tmp_path, capsys, monkeypatch):
        """Test main function with multiple paths."""
        file1 = tmp_path / "test1.py"
        file1.write_text("import os.path")

        dir2 = tmp_path / "subdir"
        dir2.mkdir()
        file2 = dir2 / "test2.py"
        file2.write_text("x = os.path.join('a', 'b')")

        monkeypatch.setattr(sys, 'argv', ['pathlint', str(file1), str(dir2)])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Processed 2 Python file(s)" in captured.out
