# File: pathlint_project/pathlint/linter.py
import argparse
import ast
import sys
from pathlib import Path
from typing import List, Set, Tuple  # For type hinting

# --- Constants ---
OFFENSE_MESSAGE: str = "\n\t!!! ARE YOU DUMB?? WHY AREN'T YOU USING PATHLIB ??? !!!\n"
PYTHON_FILE_SUFFIX: str = ".py"

# --- AST Visitor ---
class OSPathVisitor(ast.NodeVisitor):
    """
    AST visitor to find os.path usage.
    This class analyzes the abstract syntax tree of Python code
    to identify imports and attribute accesses related to 'os.path'.
    """
    def __init__(self, lines: List[str]) -> None:
        """
        Initializes the visitor.
        Args:
            lines: A list of strings representing the lines of code being analyzed.
                   Used to provide context for found offenses.
        """
        self.found_os_path: List[Tuple[int, str]] = []
        self.lines: List[str] = lines

    def _add_offense(self, node: ast.AST) -> None:
        """Helper to add an offense, ensuring line content is available."""
        if 0 <= node.lineno - 1 < len(self.lines):
            line_content: str = self.lines[node.lineno - 1].strip()
            self.found_os_path.append((node.lineno, line_content))
        else:
            # Fallback if line number is out of bounds for some reason
            self.found_os_path.append((node.lineno, "<source line not available>"))


    def visit_Import(self, node: ast.Import) -> None:
        """
        Catches 'import os.path'.
        """
        for alias in node.names:
            if alias.name == 'os.path':
                self._add_offense(node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """
        Catches 'from os import path' and 'from os.path import ...'.
        """
        if node.module == 'os':
            for alias in node.names:
                if alias.name == 'path':
                    self._add_offense(node)
        elif node.module == 'os.path':
            # Catches any import from os.path (e.g., from os.path import join, exists)
            self._add_offense(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """
        Catches direct attribute access like 'os.path.something'.
        Example: `my_var = os.path.sep`
        """
        if (isinstance(node.value, ast.Attribute) and
            isinstance(node.value.value, ast.Name) and
            node.value.value.id == 'os' and
            node.value.attr == 'path'):
            self._add_offense(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """
        Catches function calls like 'os.path.join()'.
        Example: `os.path.join("a", "b")`
        """
        if (isinstance(node.func, ast.Attribute) and
            isinstance(node.func.value, ast.Attribute) and
            isinstance(node.func.value.value, ast.Name) and
            node.func.value.value.id == 'os' and
            node.func.value.attr == 'path'):
            self._add_offense(node)
        self.generic_visit(node)

# --- Linter Logic ---
def lint_file(filepath: Path) -> List[Tuple[int, str]]:
    """
    Lints a single Python file for 'os.path' usage.

    Args:
        filepath: The Path object representing the Python file to lint.

    Returns:
        A sorted list of unique tuples, where each tuple contains
        (line_number, line_content) for each 'os.path' usage found.
        Returns an empty list if no offenses are found or if the file
        cannot be read/parsed.
    """
    try:
        content: str = filepath.read_text(encoding='utf-8')
        lines: List[str] = content.splitlines()
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        return []
    except OSError as e:
        print(f"Error reading file {filepath}: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"An unexpected error occurred while reading {filepath}: {e}", file=sys.stderr)
        return []

    try:
        tree: ast.AST = ast.parse(content, filename=str(filepath))
    except SyntaxError as e:
        print(
            f"SyntaxError in {filepath} at line {e.lineno}, offset {e.offset}: {e.msg}",
            file=sys.stderr
        )
        return []
    except Exception as e:
        print(f"An unexpected error occurred while parsing {filepath}: {e}", file=sys.stderr)
        return []


    visitor: OSPathVisitor = OSPathVisitor(lines)
    visitor.visit(tree)
    unique_offenses: Set[Tuple[int, str]] = set(visitor.found_os_path)
    return sorted(list(unique_offenses))

# --- Main Execution ---
def main() -> None:
    """
    Main command-line interface function.
    Parses arguments, collects Python files, lints them, and reports findings.
    Exits with status code 1 if offenses are found, 0 otherwise.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="A custom Python linter that scolds you for using 'os.path'."
    )
    parser.add_argument(
        "paths",
        metavar="PATH_ARG",
        nargs="+",
        type=str,
        help="One or more Python files or directories to lint."
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Suppress the custom offense message, only show findings."
    )

    args: argparse.Namespace = parser.parse_args()

    total_offenses_count: int = 0
    files_with_offenses_count: int = 0
    files_processed_count: int = 0
    files_to_lint: Set[Path] = set()

    for path_arg_str in args.paths:
        path_arg: Path = Path(path_arg_str).resolve()
        if not path_arg.exists():
            print(
                f"Error: Path '{path_arg_str}' does not exist or is not accessible.",
                file=sys.stderr
            )
            continue

        if path_arg.is_dir():
            for py_file in path_arg.rglob(f"*{PYTHON_FILE_SUFFIX}"):
                if py_file.is_file():
                    files_to_lint.add(py_file)
        elif path_arg.is_file():
            if path_arg.suffix == PYTHON_FILE_SUFFIX:
                files_to_lint.add(path_arg)
            else:
                print(f"Skipping non-Python file: {path_arg_str}", file=sys.stderr)
        else:
            print(
                f"Warning: '{path_arg_str}' is not a regular file or directory. Skipping.",
                file=sys.stderr
            )

    if not files_to_lint:
        print("No Python files found in the specified paths to lint.")
        sys.exit(0)

    sorted_files_to_lint: List[Path] = sorted(list(files_to_lint))

    for filepath in sorted_files_to_lint:
        files_processed_count += 1
        offenses: List[Tuple[int, str]] = lint_file(filepath)
        if offenses:
            files_with_offenses_count += 1
            total_offenses_count += len(offenses)
            print(f"\nOffenses found in: {filepath}")
            for lineno, line_content in offenses:
                print(f"  L{lineno}: {line_content}")
            if not args.silent:
                print(OFFENSE_MESSAGE)

    print("\n--- Linter Summary ---")
    if total_offenses_count > 0:
        print(f"Processed {files_processed_count} Python file(s).")
        print(
            f"Found a total of {total_offenses_count} 'os.path' instance(s) "
            f"in {files_with_offenses_count} file(s)."
        )
        print("Please refactor to pathlib.")
        sys.exit(1)
    else:
        print(f"Processed {files_processed_count} Python file(s).")
        print("Congratulations! No 'os.path' usage found.")
        sys.exit(0)

if __name__ == "__main__":
    main()
