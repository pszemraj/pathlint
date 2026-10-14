"""Select Python files using explicit exclusions and optional Git ignore rules."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Collection, Sequence
from fnmatch import fnmatchcase
from pathlib import Path

DEFAULT_EXCLUDES = frozenset(
    {"__pycache__", ".venv", "venv", ".git", "node_modules", ".tox", "build", "dist"}
)


def display_path(filepath: Path) -> str:
    """Format a path relative to the working directory when possible.

    :param Path filepath: File or directory path to display.
    :return str: A POSIX-style relative path, or the supplied path if it is outside cwd.
    """
    try:
        return filepath.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return filepath.as_posix()


def is_excluded(filepath: Path, patterns: Sequence[str], *, directory: bool = False) -> bool:
    """Match a path and its ancestors against case-sensitive fnmatch patterns.

    Slashless patterns match names; path patterns match working-directory-relative
    or absolute paths. A trailing slash restricts a pattern to directories.

    :param Path filepath: Absolute file or directory path to test.
    :param Sequence[str] patterns: Name or path patterns; directory matches exclude descendants.
    :param bool directory: Treat filepath itself as a directory for trailing-slash patterns.
    :return bool: True if a pattern matches the path or one of its ancestor directories.
    """
    cwd = Path.cwd()
    for candidate in (filepath, *filepath.parents):
        for pattern in patterns:
            if pattern.endswith("/") and candidate == filepath and not directory:
                continue
            pattern = pattern.rstrip("/")
            path_pattern = "/" in pattern
            if pattern.startswith("./"):
                pattern = pattern[2:]
            if Path(pattern).is_absolute():
                value = candidate.as_posix()
            else:
                value = display_path(candidate) if path_pattern else candidate.name
            if fnmatchcase(value, pattern):
                return True
        if candidate == cwd or candidate == candidate.parent:
            break
    return False


def discover_files(
    paths: Sequence[Path],
    *,
    exclude_patterns: Sequence[str] = (),
    default_excludes: Collection[str] = DEFAULT_EXCLUDES,
    respect_gitignore: bool = True,
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Collect sorted, deduplicated Python files and any discovery errors.

    Explicit files bypass automatic Git and default exclusions. Explicit patterns
    apply to every input. Directory scans use Git inside worktrees when enabled
    and available; otherwise they walk the filesystem, pruning excluded directories.

    :param Sequence[Path] paths: Files or directories to select from.
    :param Sequence[str] exclude_patterns: Explicit name or path exclusions, including for named files.
    :param Collection[str] default_excludes: Directory names excluded during automatic discovery.
    :param bool respect_gitignore: Use Git's tracked and nonignored untracked files inside worktrees when available.
    :return tuple[list[Path], list[tuple[Path, str]]]: Sorted absolute files and failing path/message pairs.
    """
    files: set[Path] = set()
    errors: list[tuple[Path, str]] = []
    visited: set[tuple[Path, Path]] = set()
    git = shutil.which("git") if respect_gitignore else None

    def excluded(candidate: Path, alias: Path, *, directory: bool = False) -> bool:
        """Check default directory exclusions and both spellings of explicit patterns.

        :param Path candidate: Resolved path being considered for automatic discovery.
        :param Path alias: Input path spelling before resolving symlinks.
        :param bool directory: Whether candidate is a directory.
        :return bool: True if default or explicit exclusions reject the candidate.
        """
        directories = candidate if directory else candidate.parent
        return (
            is_excluded(directories, tuple(default_excludes), directory=True)
            or is_excluded(candidate, exclude_patterns, directory=directory)
            or is_excluded(alias, exclude_patterns, directory=directory)
        )

    def add_file(candidate: Path, alias: Path) -> None:
        """Add a regular Python file once, honoring its resolved and alias paths.

        :param Path candidate: File path found by Git or a filesystem walk.
        :param Path alias: File spelling under the original input directory.
        """
        if not candidate.is_file() or excluded(candidate, alias):
            return
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as error:
            errors.append((candidate, str(error)))
            return
        if resolved.suffix == ".py" and not excluded(resolved, alias):
            files.add(resolved)

    def walk(root: Path, alias: Path) -> None:
        """Walk a directory, prune exclusions, and scan nested Git repositories.

        :param Path root: Resolved directory to traverse.
        :param Path alias: Directory spelling before resolving symlinks.
        """

        def onerror(error: OSError) -> None:
            """Collect a filesystem traversal failure in the discovery errors.

            :param OSError error: Failure supplied by os.walk.
            """
            errors.append((Path(error.filename or root), str(error)))

        for directory, dirs, names in os.walk(root, onerror=onerror):
            current = Path(directory)
            current_alias = alias / current.relative_to(root)
            if git and current != root and (".git" in dirs or ".git" in names):
                scan(current, current_alias)
                dirs[:] = []
                continue
            dirs[:] = [
                name
                for name in dirs
                if not excluded(current / name, current_alias / name, directory=True)
            ]
            for name in names:
                add_file(current / name, current_alias / name)

    def scan(root: Path, alias: Path) -> None:
        """Select a directory's files through Git or a filesystem walk.

        :param Path root: Resolved directory to scan once for each input spelling.
        :param Path alias: Directory spelling before resolving symlinks.
        """
        if (root, alias) in visited:
            return
        visited.add((root, alias))
        if excluded(root, alias, directory=True):
            return
        if git:
            try:
                worktree = subprocess.run(
                    [git, "-C", str(root), "rev-parse", "--show-toplevel"],
                    capture_output=True,
                    check=False,
                )
                if worktree.returncode == 0:
                    listing = subprocess.run(
                        [
                            git,
                            "-C",
                            str(root),
                            "ls-files",
                            "--cached",
                            "--others",
                            "--exclude-standard",
                            "-z",
                            "--",
                            ".",
                        ],
                        capture_output=True,
                        check=False,
                    )
                    if listing.returncode:
                        errors.append((root, listing.stderr.decode(errors="replace").strip()))
                        return
                    for item in listing.stdout.split(b"\0"):
                        if not item:
                            continue
                        candidate = root / os.fsdecode(item)
                        candidate_alias = alias / os.fsdecode(item)
                        if candidate.is_dir() and not candidate.is_symlink():
                            if not excluded(candidate, candidate_alias, directory=True):
                                scan(candidate, candidate_alias)
                        else:
                            add_file(candidate, candidate_alias)
                    return
            except OSError as error:
                errors.append((root, str(error)))
                return
        walk(root, alias)

    for path in paths:
        try:
            alias = path.absolute()
            try:
                path = path.resolve()
            except RuntimeError as error:
                # Python 3.9-3.12 report symbolic-link loops as RuntimeError.
                errors.append((path, str(error)))
                continue
            if not path.exists():
                errors.append((path, "Path does not exist"))
            elif path.is_file():
                if (
                    path.suffix == ".py"
                    and not is_excluded(path, exclude_patterns)
                    and not is_excluded(alias, exclude_patterns)
                ):
                    files.add(path)
            else:
                scan(path, alias)
        except OSError as error:
            errors.append((path, str(error)))
    return sorted(files), errors
