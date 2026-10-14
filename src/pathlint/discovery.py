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

    Args:
        filepath: File or directory path to display.

    Returns:
        A POSIX-style relative path, or the supplied path if it is outside cwd.
    """
    try:
        return filepath.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return filepath.as_posix()


def is_excluded(filepath: Path, patterns: Sequence[str], *, directory: bool = False) -> bool:
    """Match a path and its ancestors against case-sensitive fnmatch patterns.

    Slashless patterns match names; path patterns match working-directory-relative
    or absolute paths. A trailing slash restricts a pattern to directories.

    Args:
        filepath: Absolute file or directory path to test.
        patterns: Name or path patterns; directory matches exclude descendants.
        directory: Treat filepath itself as a directory for trailing-slash patterns.

    Returns:
        True if any pattern matches the path or one of its ancestor directories.
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

    Args:
        paths: Files or directories to select from.
        exclude_patterns: Explicit name or path exclusions, including for named files.
        default_excludes: Directory names excluded during automatic discovery.
        respect_gitignore: Use Git's tracked and nonignored untracked file listing
            inside worktrees when Git is available.

    Returns:
        Sorted absolute file paths and pairs of failing paths and error messages.
    """
    files: set[Path] = set()
    errors: list[tuple[Path, str]] = []
    visited: set[tuple[Path, Path]] = set()
    git = shutil.which("git") if respect_gitignore else None

    def excluded(candidate: Path, alias: Path, *, directory: bool = False) -> bool:
        """Check default directory exclusions and both spellings of explicit patterns.

        Args:
            candidate: Resolved path being considered for automatic discovery.
            alias: Input path spelling before resolving symlinks.
            directory: Whether candidate is a directory.

        Returns:
            True if default or explicit exclusions reject the candidate.
        """
        directories = candidate if directory else candidate.parent
        return (
            is_excluded(directories, tuple(default_excludes), directory=True)
            or is_excluded(candidate, exclude_patterns, directory=directory)
            or is_excluded(alias, exclude_patterns, directory=directory)
        )

    def walk(root: Path, alias: Path) -> None:
        """Walk a directory, prune exclusions, and scan nested Git repositories.

        Args:
            root: Resolved directory to traverse.
            alias: Directory spelling before resolving symlinks.
        """

        def onerror(error: OSError) -> None:
            """Collect a filesystem traversal failure in the discovery errors.

            Args:
                error: Failure supplied by os.walk.
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
                candidate = current / name
                if candidate.suffix == ".py" and not excluded(candidate, current_alias / name):
                    files.add(candidate)

    def scan(root: Path, alias: Path) -> None:
        """Select a directory's files through Git or a filesystem walk.

        Args:
            root: Resolved directory to scan once for each input spelling.
            alias: Directory spelling before resolving symlinks.
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
                        elif (
                            candidate.suffix == ".py"
                            and candidate.is_file()
                            and not excluded(candidate, candidate_alias)
                        ):
                            files.add(candidate)
                    return
            except OSError as error:
                errors.append((root, str(error)))
                return
        walk(root, alias)

    for path in paths:
        try:
            alias = path.absolute()
            path = path.resolve()
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
