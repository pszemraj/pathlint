"""Select Python files using explicit exclusions and optional Git ignore rules."""

from __future__ import annotations

import os
import re
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
    or absolute paths. Absolute patterns also match ancestors above the working
    directory. A trailing slash restricts a pattern to directories.

    :param Path filepath: Absolute file or directory path to test.
    :param Sequence[str] patterns: Name or path patterns; directory matches exclude descendants.
    :param bool directory: Treat filepath itself as a directory for trailing-slash patterns.
    :return bool: True if a pattern matches the path or one of its ancestor directories.
    """
    cwd = Path.cwd()
    above_cwd = False
    for candidate in (filepath, *filepath.parents):
        for pattern in patterns:
            if pattern.endswith("/") and candidate == filepath and not directory:
                continue
            pattern = pattern.rstrip("/") or pattern[:1]
            path_pattern = "/" in pattern
            if pattern.startswith("./"):
                pattern = pattern[2:]
            absolute = Path(pattern).is_absolute()
            if above_cwd and not absolute:
                continue
            if absolute:
                value = candidate.as_posix()
            else:
                value = display_path(candidate) if path_pattern else candidate.name
            if fnmatchcase(value, pattern):
                return True
        if candidate == cwd:
            above_cwd = True
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
        """Check default and explicit exclusions against both path spellings.

        :param Path candidate: Resolved path being considered for automatic discovery.
        :param Path alias: Input path spelling before resolving symlinks.
        :param bool directory: Whether candidate is a directory.
        :return bool: True if default or explicit exclusions reject the candidate.
        """
        directories = candidate if directory else candidate.parent
        alias_directories = alias if directory else alias.parent
        return (
            is_excluded(directories, tuple(default_excludes), directory=True)
            or is_excluded(alias_directories, tuple(default_excludes), directory=True)
            or is_excluded(candidate, exclude_patterns, directory=directory)
            or is_excluded(alias, exclude_patterns, directory=directory)
        )

    def add_file(candidate: Path, alias: Path) -> None:
        """Add a regular Python file once, honoring its resolved and alias paths.

        :param Path candidate: File path found by Git or a filesystem walk.
        :param Path alias: File spelling under the original input directory.
        """
        if excluded(candidate, alias):
            return
        try:
            resolved = candidate.resolve()
            if excluded(resolved, alias) or not resolved.is_file():
                return
        except (OSError, RuntimeError) as error:
            errors.append((candidate, str(error)))
            return
        if resolved.suffix == ".py":
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
                        env=dict(os.environ, LC_ALL="C"),
                    )
                    if listing.returncode:
                        errors.append((root, listing.stderr.decode(errors="replace").strip()))
                        return
                    directory_warning = rb"warning: could not open directory '(.*?)': [^\n]*\n"
                    worktree_root = Path(os.fsdecode(worktree.stdout.removesuffix(b"\n")))
                    for warning in re.finditer(directory_warning, listing.stderr, re.DOTALL):
                        candidate = worktree_root / os.fsdecode(warning[1])
                        candidate_alias = alias / candidate.relative_to(root)
                        if not excluded(candidate, candidate_alias, directory=True):
                            errors.append((candidate, warning[0].decode(errors="replace").strip()))
                    remaining = re.sub(directory_warning, b"", listing.stderr, flags=re.DOTALL)
                    ignore_warning = rb"warning: unable to access '(.*?)': [^\n]*\n"
                    global_ignore_config = None
                    for warning in re.finditer(ignore_warning, remaining, re.DOTALL):
                        candidate = worktree_root / os.fsdecode(warning[1])
                        if candidate.name != ".gitignore":
                            continue
                        try:
                            candidate_alias = alias / candidate.relative_to(root)
                        except ValueError:
                            continue
                        if not excluded(candidate.parent, candidate_alias.parent, directory=True):
                            continue
                        if global_ignore_config is None:
                            global_ignore_config = subprocess.run(
                                [
                                    git,
                                    "-C",
                                    str(root),
                                    "config",
                                    "--path",
                                    "--null",
                                    "--get",
                                    "core.excludesFile",
                                ],
                                capture_output=True,
                                check=False,
                            )
                        if global_ignore_config.returncode == 0:
                            global_ignore = worktree_root / os.fsdecode(
                                global_ignore_config.stdout.removesuffix(b"\0")
                            )
                            try:
                                if candidate.resolve() == global_ignore.resolve():
                                    continue
                            except (OSError, RuntimeError):
                                continue
                        elif global_ignore_config.returncode != 1:
                            continue
                        remaining = remaining.replace(warning[0], b"")
                    if remaining.strip():
                        errors.append((root, remaining.decode(errors="replace").strip()))
                    for item in listing.stdout.split(b"\0"):
                        if not item:
                            continue
                        candidate = root / os.fsdecode(item)
                        candidate_alias = alias / os.fsdecode(item)
                        if excluded(candidate.parent, candidate_alias.parent, directory=True):
                            continue
                        try:
                            # Outer indexes can contain individual files beneath an
                            # embedded repository instead of a single gitlink entry.
                            nested = root
                            for part in candidate.relative_to(root).parts[:-1]:
                                nested /= part
                                if (nested / ".git").exists():
                                    scan(nested, alias / nested.relative_to(root))
                                    break
                            else:
                                if excluded(candidate, candidate_alias):
                                    continue
                                if not candidate.is_symlink() and candidate.is_dir():
                                    if not excluded(candidate, candidate_alias, directory=True):
                                        scan(candidate, candidate_alias)
                                else:
                                    add_file(candidate, candidate_alias)
                        except OSError as error:
                            if not excluded(candidate, candidate_alias):
                                errors.append((candidate, str(error)))
                    return
                if any(
                    (parent / ".git").is_file()
                    or (parent / ".git/HEAD").exists()
                    or (parent / ".git/objects").is_dir()
                    for parent in (root, *root.parents)
                ):
                    errors.append((root, worktree.stderr.decode(errors="replace").strip()))
                    return
            except OSError as error:
                errors.append((root, str(error)))
                return
        walk(root, alias)

    for path in paths:
        alias = path
        try:
            alias = path.absolute()
            try:
                path = path.resolve()
            except RuntimeError as error:
                # Python 3.9-3.12 report symbolic-link loops as RuntimeError.
                if not is_excluded(alias, exclude_patterns):
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
            if not is_excluded(path, exclude_patterns) and not is_excluded(alias, exclude_patterns):
                errors.append((path, str(error)))
    return sorted(files), errors
