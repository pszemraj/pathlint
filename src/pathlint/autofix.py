#!/usr/bin/env python3
"""Auto-fixer for common os.path patterns to pathlib equivalents."""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

# Common os.path to pathlib replacements
REPLACEMENTS: List[Tuple[str, str]] = [
    # Import statements
    (r"^import os\.path$", "from pathlib import Path"),
    (r"^from os import path$", "from pathlib import Path"),
    (r"^from os\.path import (.+)$", "from pathlib import Path"),
    # Function replacements
    (r"os\.path\.exists\(([^)]+)\)", r"Path(\1).exists()"),
    (r"os\.path\.isfile\(([^)]+)\)", r"Path(\1).is_file()"),
    (r"os\.path\.isdir\(([^)]+)\)", r"Path(\1).is_dir()"),
    (r"os\.path\.isabs\(([^)]+)\)", r"Path(\1).is_absolute()"),
    (r"os\.path\.basename\(([^)]+)\)", r"Path(\1).name"),
    (r"os\.path\.dirname\(([^)]+)\)", r"str(Path(\1).parent)"),
    (r"os\.path\.abspath\(([^)]+)\)", r"str(Path(\1).resolve())"),
    (r"os\.path\.expanduser\(([^)]+)\)", r"str(Path(\1).expanduser())"),
    (r"os\.path\.splitext\(([^)]+)\)", r"(Path(\1).stem, Path(\1).suffix)"),
    # Join patterns - handle 2 and 3 arguments
    (r"os\.path\.join\(([^,]+),\s*([^,]+),\s*([^)]+)\)", r"str(Path(\1) / \2 / \3)"),
    (r"os\.path\.join\(([^,]+),\s*([^)]+)\)", r"str(Path(\1) / \2)"),
    # Path attributes
    (r"os\.path\.sep", 'Path.sep if hasattr(Path, "sep") else "/"'),
    (r"os\.path\.pathsep", 'Path.pathsep if hasattr(Path, "pathsep") else ":"'),
    # Handle 'from os import path' usage
    (r"\bpath\.exists\(([^)]+)\)", r"Path(\1).exists()"),
    (r"\bpath\.isfile\(([^)]+)\)", r"Path(\1).is_file()"),
    (r"\bpath\.isdir\(([^)]+)\)", r"Path(\1).is_dir()"),
    (r"\bpath\.join\(([^,]+),\s*([^)]+)\)", r"str(Path(\1) / \2)"),
    (r"\bpath\.basename\(([^)]+)\)", r"Path(\1).name"),
    (r"\bpath\.dirname\(([^)]+)\)", r"str(Path(\1).parent)"),
]


def add_pathlib_import(content: str) -> str:
    """Add pathlib import if not already present."""
    if "from pathlib import" not in content and "import pathlib" not in content:
        lines = content.splitlines()
        # Find the last import line
        last_import_idx = -1
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                last_import_idx = i

        if last_import_idx >= 0:
            # Add after last import
            lines.insert(last_import_idx + 1, "from pathlib import Path")
        else:
            # Add at the beginning
            lines.insert(0, "from pathlib import Path")

        return "\n".join(lines)
    return content


def fix_file(filepath: Path, dry_run: bool = False) -> int:
    """
    Apply auto-fixes to a Python file.

    Returns:
        Number of replacements made.
    """
    try:
        original_content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"✗ Cannot read {filepath}: {e.__class__.__name__}", file=sys.stderr)
        return 0

    content = original_content
    total_replacements = 0

    # Apply each replacement pattern
    for pattern, replacement in REPLACEMENTS:
        new_content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)
        if count > 0:
            content = new_content
            total_replacements += count

    # Add pathlib import if we made replacements
    if total_replacements > 0 and "Path(" in content:
        content = add_pathlib_import(content)

    if total_replacements > 0:
        if dry_run:
            print(f"\n{filepath}: {total_replacements} replacement(s) would be made")
            # Show diff preview
            import difflib

            diff = difflib.unified_diff(
                original_content.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=str(filepath),
                tofile=str(filepath) + " (fixed)",
                lineterm="",
            )
            for line in diff:
                if line.startswith("+") and not line.startswith("+++"):
                    print(f"  \033[92m{line}\033[0m", end="")
                elif line.startswith("-") and not line.startswith("---"):
                    print(f"  \033[91m{line}\033[0m", end="")
                else:
                    print(f"  {line}", end="")
        else:
            filepath.write_text(content, encoding="utf-8")
            print(f"✓ Fixed {filepath}: {total_replacements} replacement(s)")

    return total_replacements


def main() -> None:
    """CLI for auto-fixing os.path usage."""
    parser = argparse.ArgumentParser(
        description="Auto-fix os.path usage to pathlib",
        epilog="WARNING: This tool makes automated changes. Review carefully!",
    )
    parser.add_argument("paths", nargs="+", help="Files or directories to fix")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be changed without modifying files"
    )
    parser.add_argument("--no-color", action="store_true", help="Disable colored diff output")

    args = parser.parse_args()

    # Collect Python files
    from pathlint.linter import find_python_files

    files = find_python_files(args.paths)

    if not files:
        print("No Python files found to fix")
        sys.exit(2)

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
        print("\nRun without --dry-run to apply changes")
    else:
        if total_replacements > 0:
            print(f"✓ Fixed {total_files_fixed} file(s)")
            print(f"✓ Made {total_replacements} replacement(s)")
            print("\n⚠️  Please review changes and test your code!")
        else:
            print("✓ No os.path usage found to fix")


if __name__ == "__main__":
    main()
