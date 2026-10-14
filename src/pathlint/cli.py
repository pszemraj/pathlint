"""Check Python files for os.path usage or migrate supported calls to pathlib."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from pathlint.discovery import DEFAULT_EXCLUDES, discover_files, display_path
from pathlint.linter import _lint_file


def _legacy_args(argv: list[str]) -> list[str]:
    if argv and argv[0] in {"check", "fix", "files", "-h", "--help", "--version"}:
        return argv
    normalized = []
    fix = False
    value_pending = False
    for index, arg in enumerate(argv):
        if value_pending:
            normalized.append(arg)
            value_pending = False
        elif arg == "--":
            normalized.extend(argv[index:])
            break
        elif arg == "--fix":
            fix = True
        else:
            normalized.append(arg)
            if arg == "--dry-run":
                fix = True
            value_pending = arg in {"-e", "--exclude", "--output-format"}
    return ["fix" if fix else "check", *normalized]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse canonical commands and legacy invocation aliases."""
    parser = argparse.ArgumentParser(
        prog="pathlint",
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="Bare pathlint checks '.'. Legacy pathlint PATH and --fix/--dry-run remain supported.",
    )
    try:
        installed_version = version("pathlint")
    except PackageNotFoundError:
        installed_version = "unknown (not installed)"
    parser.add_argument("--version", action="version", version=f"%(prog)s {installed_version}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for command, help_text in (
        ("check", "Detect os.path usage"),
        ("fix", "Apply experimental pathlib migrations"),
        ("files", "List selected Python files without linting"),
    ):
        subparser = commands.add_parser(
            command,
            help=help_text,
            description=help_text,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
        subparser.add_argument(
            "paths",
            nargs="*",
            type=Path,
            default=argparse.SUPPRESS,
            metavar="PATH",
            help="Files or directories (default: .)",
        )
        subparser.add_argument(
            "-e",
            "--exclude",
            action="append",
            default=[],
            metavar="PATTERN",
            help="Exclude a name or fnmatch path pattern; repeat for multiple patterns",
        )
        subparser.add_argument(
            "--no-gitignore", action="store_true", help="Ignore Git exclusion rules"
        )
        subparser.add_argument(
            "--no-default-excludes",
            action="store_true",
            help="Disable built-in directory exclusions",
        )
        if command != "files":
            subparser.add_argument(
                "-q",
                "--quiet",
                action="store_true",
                help="Suppress summaries, retaining findings or diffs",
            )
        if command == "check":
            subparser.add_argument(
                "--output-format", choices=("text", "json"), default="text", help="Report format"
            )
            subparser.add_argument(
                "--stats", action="store_true", help="Show the five files with the most violations"
            )
            subparser.add_argument(
                "--aggressive",
                action="store_true",
                help="Show an aggressive message when violations are found",
            )
        elif command == "fix":
            subparser.add_argument(
                "--dry-run",
                action="store_true",
                help="Print diffs without writing files; exit 1 if changes are proposed",
            )
            subparser.add_argument("--no-color", action="store_true", help="Disable colored diffs")
    args = parser.parse_args(_legacy_args(list(sys.argv[1:] if argv is None else argv)))
    if not getattr(args, "paths", None):
        args.paths = [Path(".")]
    if args.command == "check" and args.output_format == "json" and (args.quiet or args.aggressive):
        parser.error("--output-format json cannot be combined with --quiet or --aggressive")
    return args


def _error(path: Path, error: Exception | str) -> dict[str, Any]:
    return {
        "path": display_path(path),
        "line": error.lineno if isinstance(error, SyntaxError) else None,
        "message": str(error),
    }


def _print_errors(errors: list[dict[str, Any]]) -> None:
    for error in errors:
        location = error["path"]
        if error["line"] is not None:
            location += f":{error['line']}"
        print(f"{location}: error: {error['message']}", file=sys.stderr)


def _check(
    files: list[Path],
    errors: list[dict[str, Any]],
    *,
    output_format: str,
    quiet: bool,
    stats: bool,
    aggressive: bool,
) -> int:
    diagnostics = []
    counts: dict[str, int] = {}
    checked = 0
    for filepath in files:
        try:
            offenses = _lint_file(filepath)
        except (OSError, UnicodeError, SyntaxError) as error:
            errors.append(_error(filepath, error))
            continue
        checked += 1
        label = display_path(filepath)
        if offenses:
            counts[label] = len(offenses)
        for line, source in offenses:
            diagnostics.append(
                {
                    "path": label,
                    "line": line,
                    "code": "PL001",
                    "message": "Use pathlib instead of os.path",
                    "source": source,
                }
            )
    summary = {
        "files_checked": checked,
        "files_with_issues": len(counts),
        "violations": len(diagnostics),
        "errors": len(errors),
    }
    statistics = [
        {"path": path, "violations": count}
        for path, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    if output_format == "json":
        report = {"diagnostics": diagnostics, "errors": errors, "summary": summary}
        if stats:
            report["statistics"] = statistics
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        _print_errors(errors)
        for diagnostic in diagnostics:
            print(
                f"{diagnostic['path']}:{diagnostic['line']}: PL001 os.path usage: {diagnostic['source']}"
            )
        if not quiet:
            if diagnostics:
                noun = "violation" if len(diagnostics) == 1 else "violations"
                print(
                    f"Found {len(diagnostics)} {noun} in {len(counts)} of {checked} files checked."
                )
                if aggressive:
                    print("⚠️  ARE YOU KIDDING ME? USE PATHLIB! ⚠️")
            elif errors:
                print(f"Checked {checked} files; check incomplete.")
            elif not files:
                print("No Python files selected.")
            else:
                print(f"Checked {checked} files; no os.path usage found.")
            if errors:
                print(f"Errors: {len(errors)}")
            if stats and statistics:
                print("Worst offenders:")
                for entry in statistics:
                    print(f"  {entry['violations']} - {entry['path']}")
    return 2 if errors else int(bool(diagnostics))


def _fix(
    files: list[Path],
    errors: list[dict[str, Any]],
    *,
    dry_run: bool,
    quiet: bool,
    no_color: bool,
) -> int:
    from pathlint.autofix import fix_content

    fixed = replacements = 0
    color = sys.stdout.isatty() and not no_color
    for filepath in files:
        try:
            with filepath.open(encoding="utf-8", newline="") as stream:
                original = stream.read()
            updated, count = fix_content(original)
            if count and not dry_run:
                with filepath.open("w", encoding="utf-8", newline="") as stream:
                    stream.write(updated)
        except (OSError, UnicodeError, SyntaxError) as error:
            errors.append(_error(filepath, error))
            continue
        if not count:
            continue
        fixed += 1
        replacements += count
        label = display_path(filepath)
        if dry_run:
            for line in difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=label,
                tofile=f"{label} (fixed)",
            ):
                line = line.rstrip("\r\n")
                if color and line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                    ansi = "92" if line.startswith("+") else "91"
                    line = f"\033[{ansi}m{line}\033[0m"
                print(line)
        elif not quiet:
            print(f"Fixed {label}: {count} replacements.")
    _print_errors(errors)
    if not quiet:
        action = "Would make" if dry_run else "Made"
        print(f"{action} {replacements} replacements in {fixed} files.")
        if fixed and not dry_run:
            print("Review the changes and test your code.")
        if errors:
            print(f"Errors: {len(errors)}")
    return 2 if errors else int(dry_run and replacements > 0)


def main(argv: list[str] | None = None) -> int:
    """Run a command and return its exit status."""
    try:
        args = parse_args(argv)
        files, discovery_errors = discover_files(
            args.paths,
            exclude_patterns=args.exclude,
            default_excludes=() if args.no_default_excludes else DEFAULT_EXCLUDES,
            respect_gitignore=not args.no_gitignore,
        )
        errors = [_error(path, message) for path, message in discovery_errors]
        if args.command == "files":
            for filepath in files:
                print(display_path(filepath))
            _print_errors(errors)
            return 2 if errors else 0
        if args.command == "fix":
            return _fix(
                files, errors, dry_run=args.dry_run, quiet=args.quiet, no_color=args.no_color
            )
        return _check(
            files,
            errors,
            output_format=args.output_format,
            quiet=args.quiet,
            stats=args.stats,
            aggressive=args.aggressive,
        )
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # Keep the replacement stream open until interpreter shutdown.
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        return 1
