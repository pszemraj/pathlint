"""Exercise selection against real files and Git worktrees."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pathlint.discovery import DEFAULT_EXCLUDES, discover_files, display_path


def write(root: Path, name: str) -> Path:
    filepath = root / name
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text("pass\n", encoding="utf-8")
    return filepath


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("git") is None:
        pytest.skip("Git is required for Git discovery tests")
    # Avoid inheriting a user's global excludes or repository selection.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    monkeypatch.chdir(root)
    return root


def test_repeatable_exclusions_and_overlapping_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    expected = [write(tmp_path, "a.py"), write(tmp_path, "src/z.py")]
    write(tmp_path, "vendor/no.py")
    write(tmp_path, "nested/vendor/no.py")
    write(tmp_path, "training/wandb/deep/no.py")
    write(tmp_path, "backups/no.py")
    write(tmp_path, "readme.txt")
    files, errors = discover_files(
        [tmp_path, tmp_path / "src", expected[0]],
        exclude_patterns=("vendor", "training/wandb/*", "backups"),
        respect_gitignore=False,
    )
    assert files == expected
    assert errors == []


@pytest.mark.parametrize("pattern", ["src/*", "src/", "./src", "src"])
def test_directory_match_excludes_descendants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pattern: str
) -> None:
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "src/deep/no.py")
    wanted = write(tmp_path, "keep.py")
    assert discover_files([tmp_path], exclude_patterns=(pattern,)) == ([wanted], [])


def test_path_pattern_is_cwd_relative_not_scan_root_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    no = write(tmp_path, "project/src/no.py")
    wanted = write(tmp_path, "project/deep/src/keep.py")
    assert discover_files([tmp_path / "project"], exclude_patterns=("project/src",)) == (
        [wanted],
        [],
    )
    assert discover_files([tmp_path / "project"], exclude_patterns=("src/*",)) == (
        sorted([no, wanted]),
        [],
    )


def test_dot_slash_name_is_anchored_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "vendor/no.py")
    wanted = write(tmp_path, "nested/vendor/keep.py")
    assert discover_files([tmp_path], exclude_patterns=("./vendor",)) == ([wanted], [])


def test_directory_only_pattern_keeps_matching_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    wanted = write(tmp_path, "cache.py")
    write(tmp_path, "nested/cache.py/no.py")
    assert discover_files([tmp_path], exclude_patterns=("cache.py/",)) == ([wanted], [])
    assert discover_files([wanted], exclude_patterns=("cache.py/",)) == ([wanted], [])


@pytest.mark.parametrize("external", [False, True])
def test_absolute_pattern_matches_internal_and_external_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, external: bool
) -> None:
    root = tmp_path / "scan"
    excluded = write(root, "vendor/no.py")
    wanted = write(root, "keep.py")
    monkeypatch.chdir(tmp_path if external else root)
    assert discover_files([root], exclude_patterns=(str(excluded.parent),)) == ([wanted], [])
    assert discover_files([excluded], exclude_patterns=(str(excluded),)) == ([], [])


def test_patterns_are_case_sensitive_and_have_no_negation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    wanted = write(tmp_path, "Vendor/keep.py")
    write(tmp_path, "vendor/no.py")
    write(tmp_path, "!vendor/no.py")
    assert discover_files([tmp_path], exclude_patterns=("vendor", "!vendor")) == ([wanted], [])


def test_default_excludes_and_opt_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    wanted = write(tmp_path, "keep.py")
    hidden = [write(tmp_path, f"{name}/nested/file.py") for name in DEFAULT_EXCLUDES]
    assert discover_files([tmp_path], respect_gitignore=False) == ([wanted], [])
    assert discover_files([tmp_path], default_excludes=(), respect_gitignore=False) == (
        sorted([wanted, *hidden]),
        [],
    )
    assert discover_files([tmp_path / "build/nested"]) == ([], [])


def test_explicit_file_bypasses_defaults_but_not_cli_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    filepath = write(tmp_path, "build/nested/file.py")
    assert discover_files([filepath]) == ([filepath], [])
    assert discover_files([filepath], exclude_patterns=("build",)) == ([], [])


def test_missing_inputs_do_not_discard_valid_files(tmp_path: Path) -> None:
    wanted = write(tmp_path, "keep.py")
    missing = tmp_path / "missing.py"
    files, errors = discover_files([missing, wanted])
    assert files == [wanted]
    assert errors == [(missing, "Path does not exist")]


def test_no_git_binary_falls_back_to_walk(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    wanted = write(repo, "ignored/keep.py")
    monkeypatch.setattr("pathlint.discovery.shutil.which", lambda _: None)
    assert discover_files([repo]) == ([wanted], [])


def test_git_ignores_negation_tracked_and_deleted_files(repo: Path) -> None:
    (repo / ".gitignore").write_text(
        "ignored/\n*.py\n!keep.py\n!nested/\n!nested/keep.py\n", encoding="utf-8"
    )
    tracked = write(repo, "ignored/tracked.py")
    deleted = write(repo, "deleted.py")
    git(repo, "add", "-f", "ignored/tracked.py", "deleted.py")
    deleted.unlink()
    keep = write(repo, "keep.py")
    nested = write(repo, "nested/keep.py")
    write(repo, "ignored/untracked.py")
    write(repo, "nested/hidden.py")
    assert discover_files([repo]) == (sorted([tracked, keep, nested]), [])
    assert discover_files([repo / "nested"]) == ([nested], [])
    explicit = repo / "ignored/untracked.py"
    assert discover_files([explicit]) == ([explicit], [])
    assert discover_files([explicit], exclude_patterns=("ignored/",)) == ([], [])


def test_git_and_default_exclusion_opt_outs_are_independent(repo: Path) -> None:
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    built = write(repo, "build/generated.py")
    ignored = write(repo, "ignored/snapshot.py")
    wanted = write(repo, "main.py")
    assert discover_files([repo]) == ([wanted], [])
    assert discover_files([repo], default_excludes=()) == (sorted([wanted, built]), [])
    assert discover_files([repo], respect_gitignore=False) == (sorted([wanted, ignored]), [])


def test_nested_untracked_repository_uses_its_own_ignores(repo: Path) -> None:
    wanted = write(repo, "nested/keep.py")
    git(repo / "nested", "init", "-q")
    (repo / "nested/.gitignore").write_text("ignored.py\n", encoding="utf-8")
    write(repo, "nested/ignored.py")
    assert discover_files([repo]) == ([wanted], [])
    assert discover_files([repo], exclude_patterns=("nested/",)) == ([], [])
    (repo / ".gitignore").write_text("nested/\n", encoding="utf-8")
    assert discover_files([repo]) == ([], [])


def test_tracked_nested_repository_is_discovered(repo: Path) -> None:
    nested = repo / "nested"
    wanted = write(repo, "nested/keep.py")
    git(nested, "init", "-q")
    git(nested, "add", "keep.py")
    git(
        nested, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init"
    )
    # Git stores an added repository as a gitlink, just like a submodule.
    git(repo, "add", "nested")
    assert discover_files([repo]) == ([wanted], [])


def test_git_handles_newlines_and_spaces_in_filenames(repo: Path) -> None:
    wanted = [write(repo, "with space.py"), write(repo, "with\nnewline.py")]
    assert discover_files([repo]) == (sorted(wanted), [])


def test_walk_prunes_excluded_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "excluded/nested/no.py")
    wanted = write(tmp_path, "keep.py")
    original_walk = os.walk
    visited = []

    def observed_walk(*args, **kwargs):
        for row in original_walk(*args, **kwargs):
            visited.append(Path(row[0]))
            yield row

    monkeypatch.setattr("pathlint.discovery.os.walk", observed_walk)
    assert discover_files([tmp_path], exclude_patterns=("excluded",), respect_gitignore=False) == (
        [wanted],
        [],
    )
    assert visited == [tmp_path]


def test_walk_errors_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_walk(root, *, onerror):
        onerror(PermissionError(13, "Permission denied", str(root / "blocked")))
        return iter(())

    monkeypatch.setattr("pathlint.discovery.os.walk", broken_walk)
    files, errors = discover_files([tmp_path], respect_gitignore=False)
    assert files == []
    assert errors[0][0] == tmp_path / "blocked"
    assert "Permission denied" in errors[0][1]


def test_display_path_uses_relative_internal_and_absolute_external_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert display_path(cwd / "src/file.py") == "src/file.py"
    assert display_path(tmp_path / "other.py") == str(tmp_path / "other.py")


def test_walk_switches_to_git_for_worktree_beneath_nonrepository(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    wanted = write(repo, "keep.py")
    write(repo, "ignored.py")
    monkeypatch.chdir(repo.parent)
    assert discover_files([repo.parent]) == ([wanted], [])


def test_git_listing_errors_are_reported(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_run = subprocess.run

    def fail_listing(args, **kwargs):
        if "ls-files" in args:
            return subprocess.CompletedProcess(args, 128, b"", b"fatal: cannot read index\n")
        return original_run(args, **kwargs)

    monkeypatch.setattr("pathlint.discovery.subprocess.run", fail_listing)
    assert discover_files([repo]) == ([], [(repo, "fatal: cannot read index")])


def test_git_execution_errors_are_reported(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_git(*args, **kwargs):
        raise PermissionError("Git executable is inaccessible")

    monkeypatch.setattr("pathlint.discovery.subprocess.run", fail_git)
    assert discover_files([repo]) == ([], [(repo, "Git executable is inaccessible")])


def test_resolution_errors_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_resolve(self):
        raise PermissionError("Path is inaccessible")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    assert discover_files([tmp_path]) == ([], [(tmp_path, "Path is inaccessible")])


@pytest.mark.parametrize("pattern", ["alias.py", "./alias.py", "links/*.py"])
def test_explicit_file_symlink_preserves_alias_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pattern: str
) -> None:
    monkeypatch.chdir(tmp_path)
    target = write(tmp_path, "real/target.py")
    alias = tmp_path / ("links/alias.py" if pattern.startswith("links/") else "alias.py")
    alias.parent.mkdir(exist_ok=True)
    alias.symlink_to(target)
    assert discover_files([alias]) == ([target], [])
    assert discover_files([alias], exclude_patterns=(pattern,)) == ([], [])
    assert discover_files([alias], exclude_patterns=("target.py",)) == ([], [])
    assert discover_files([alias, target]) == ([target], [])


@pytest.mark.parametrize("respect_gitignore", [False, True])
@pytest.mark.parametrize("pattern", ["alias/", "alias/*.py", "./alias/deep", "alias/*/no.py"])
def test_directory_symlink_preserves_root_and_descendant_alias_patterns(
    repo: Path, pattern: str, respect_gitignore: bool
) -> None:
    no = write(repo, "real/deep/no.py")
    wanted = write(repo, "real/keep.py")
    alias = repo / "alias"
    alias.symlink_to(repo / "real", target_is_directory=True)
    assert discover_files([alias], respect_gitignore=respect_gitignore) == (
        sorted([no, wanted]),
        [],
    )
    expected = [] if pattern in ("alias/", "alias/*.py") else [wanted]
    assert discover_files(
        [alias], exclude_patterns=(pattern,), respect_gitignore=respect_gitignore
    ) == (expected, [])
    # A partial alias exclusion must not suppress a second spelling of the root.
    assert discover_files(
        [alias, repo / "real"],
        exclude_patterns=(pattern,),
        respect_gitignore=respect_gitignore,
    ) == (sorted([no, wanted]), [])
