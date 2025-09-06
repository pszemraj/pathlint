#!/usr/bin/env python3
"""Fast os.path detector with type annotation support."""

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class OSPathDetector(ast.NodeVisitor):
    """Single-pass AST visitor that catches ALL os.path usage patterns."""

    def __init__(self, lines: List[str]) -> None:
        self.offenses: Dict[int, Set[str]] = defaultdict(set)  # Dedupes automatically
        self.lines = lines
        self.os_imported = False
        self.path_aliases: Set[str] = set()  # Track 'path', 'ospath', etc.

    def _record(self, node: ast.AST, context: str = "") -> None:
        """Record unique offense by line number."""
        if hasattr(node, "lineno"):
            line_idx = node.lineno - 1
            if 0 <= line_idx < len(self.lines):
                line = self.lines[line_idx].strip()
                self.offenses[node.lineno].add(line)

    def visit_Import(self, node: ast.Import) -> None:
        """Detect: import os, import os.path, import os.path as X"""
        for alias in node.names:
            if alias.name == "os":
                self.os_imported = True  # NOW we can detect os.path.X
            elif alias.name == "os.path":
                self._record(node)
                self.path_aliases.add(alias.asname or "os.path")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Detect: from os import path [as X], from os.path import ..."""
        if node.module == "os":
            for alias in node.names:
                if alias.name == "path":
                    self._record(node)
                    self.path_aliases.add(alias.asname or "path")
        elif node.module and "os.path" in node.module:
            self._record(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Detect: os.path.X, aliased_path.X"""
        # Direct os.path usage
        if isinstance(node.value, ast.Attribute):
            if (
                isinstance(node.value.value, ast.Name)
                and node.value.value.id == "os"
                and node.value.attr == "path"
            ):
                self._record(node)
        # Aliased path usage
        elif isinstance(node.value, ast.Name) and node.value.id in self.path_aliases:
            self._record(node)
        self.generic_visit(node)

    # TYPE ANNOTATION SUPPORT (Original doesn't have this!)
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Detect os.path in type annotations: x: os.path.PathLike"""
        self._check_annotation(node.annotation, node)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        """Detect os.path in function argument annotations."""
        if node.annotation:
            self._check_annotation(node.annotation, node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Detect os.path in return type annotations."""
        if node.returns:
            self._check_annotation(node.returns, node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Detect os.path in async function return type annotations."""
        if node.returns:
            self._check_annotation(node.returns, node)
        self.generic_visit(node)

    def _check_annotation(self, annotation: ast.AST, source_node: ast.AST) -> None:
        """Recursively check annotations for os.path."""
        for child in ast.walk(annotation):
            if isinstance(child, ast.Attribute):
                self.visit_Attribute(child)


def lint_file(filepath: Path) -> List[Tuple[int, str]]:
    """Fast single-pass lint with early termination."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"✗ Cannot read {filepath}: {e.__class__.__name__}", file=sys.stderr)
        return []

    lines = content.splitlines()

    # PERFORMANCE: Skip files without 'os' or 'path' strings
    if "os" not in content and "path" not in content:
        return []

    try:
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError as e:
        print(f"✗ Syntax error in {filepath}:{e.lineno}:{e.offset}", file=sys.stderr)
        return []

    detector = OSPathDetector(lines)
    detector.visit(tree)

    # Convert to sorted list (no duplicates thanks to Set)
    result = []
    for lineno in sorted(detector.offenses.keys()):
        line_content = next(iter(detector.offenses[lineno]))
        result.append((lineno, line_content))

    return result


def find_python_files(paths: List[str], exclude_patterns: Optional[Set[str]] = None) -> Set[Path]:
    """Efficiently collect Python files with smart exclusions."""
    if exclude_patterns is None:
        exclude_patterns = {
            "__pycache__",
            ".venv",
            "venv",
            ".git",
            "node_modules",
            ".tox",
            "build",
            "dist",
        }

    files = set()
    for path_str in paths:
        path = Path(path_str).resolve()
        if not path.exists():
            print(f"✗ Path does not exist: {path_str}", file=sys.stderr)
            continue
        if path.is_file():
            if path.suffix == ".py":
                files.add(path)
        else:
            # Smart exclusion during traversal
            for py_file in path.rglob("*.py"):
                if not any(part in exclude_patterns for part in py_file.parts):
                    files.add(py_file)
    return files


def main_autofix(args: argparse.Namespace) -> int:
    """Handle autofix mode separately."""
    from pathlint.autofix import fix_file

    files = find_python_files(args.paths)
    if not files:
        print("No Python files found to fix")
        return 2

    total_files_fixed = 0
    total_replacements = 0

    for filepath in sorted(files):
        replacements = fix_file(filepath, args.dry_run)
        if replacements > 0:
            total_files_fixed += 1
            total_replacements += replacements

    print(f"\n{'─' * 40}")

    if args.dry_run:
        print("Dry run complete:")
        print(f"  Would fix {total_files_fixed} file(s)")
        print(f"  Would make {total_replacements} replacement(s)")
        print("\nRun with --fix to apply changes")
    else:
        if total_replacements > 0:
            print(f"✓ Fixed {total_files_fixed} file(s)")
            print(f"✓ Made {total_replacements} replacement(s)")
            print("\n⚠️  Please review changes and test your code!")
        else:
            print("✓ No os.path usage found to fix")

    # Return 0 if fixes were successfully applied (or no fixes needed)
    return 0


def main() -> None:
    """CLI with professional output and useful features."""
    parser = argparse.ArgumentParser(
        description="Detect os.path usage in Python files",
        epilog="Exit codes: 0 = clean, 1 = os.path found, 2 = errors",
    )
    parser.add_argument("paths", nargs="+", help="Files or directories to check")
    parser.add_argument(
        "--aggressive",
        action="store_true",
        help="Show aggressive message when os.path is found",
    )
    parser.add_argument("--stats", action="store_true", help="Show detailed statistics")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically fix os.path usage (modifies files!)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what --fix would change without modifying files",
    )

    args = parser.parse_args()

    # Handle --fix mode separately
    if args.fix or args.dry_run:
        sys.exit(main_autofix(args))

    # Normal linting mode
    files = find_python_files(args.paths)
    if not files:
        print("No Python files found to check")
        sys.exit(2)

    total_offenses = 0
    files_with_offenses = []

    for filepath in sorted(files):
        offenses = lint_file(filepath)
        if offenses:
            files_with_offenses.append(filepath)
            total_offenses += len(offenses)
            print(f"\n{filepath}")
            for lineno, line in offenses:
                print(f"  L{lineno:4d}: {line}")

    print(f"\n{'─' * 40}")

    if total_offenses > 0:
        if args.aggressive:
            print("\n⚠️  ARE YOU KIDDING ME? USE PATHLIB! ⚠️\n")

        print(f"Files checked:     {len(files)}")
        print(f"Files with issues: {len(files_with_offenses)}")
        print(f"Total violations:  {total_offenses}")

        if args.stats and files_with_offenses:
            print("\nWorst offenders:")
            file_counts = {}
            for f in files_with_offenses:
                count = len(lint_file(f))
                if count > 0:
                    file_counts[f] = count
            for f, count in sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
                print(f"  {count:3d} - {f.name}")

        print("\n✗ Found os.path usage. Migrate to pathlib.")
        sys.exit(1)
    else:
        print(f"✓ {len(files)} files checked - no os.path usage found!")
        sys.exit(0)


if __name__ == "__main__":
    main()
