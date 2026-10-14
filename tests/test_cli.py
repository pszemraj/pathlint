"""Exercise command behavior, reporting, compatibility, and stream contracts."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

import pathlint
from pathlint import cli

OFFENDING = "import os\nvalue = os.path.exists('example')\n"


def test_main_module_import_does_not_run_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden() -> int:
        pytest.fail("Importing pathlint.__main__ must not run the CLI")

    monkeypatch.setattr(cli, "main", forbidden)
    try:
        importlib.import_module("pathlint.__main__")
    finally:
        sys.modules.pop("pathlint.__main__", None)
    assert capsys.readouterr() == ("", "")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_file(root: Path, name: str, content: str = OFFENDING) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def invoke(root: Path, *args: str, encoding: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the module in a subprocess, optionally forcing its output encoding.

    :param Path root: Working directory for the command.
    :param str args: Command-line arguments without the executable name.
    :param str | None encoding: Stream encoding to force with UTF-8 mode disabled.
    :return subprocess.CompletedProcess[str]: Captured output and process status.
    """
    env = dict(os.environ, PYTHONPATH=str(Path(cli.__file__).parents[1]))
    if encoding is not None:
        env.update(PYTHONUTF8="0", PYTHONIOENCODING=f"{encoding}:strict")
    if "COV_CORE_SOURCE" in env:
        # These subprocesses change cwd; retain the parent's branch-coverage settings.
        env["COV_CORE_CONFIG"] = str(Path(__file__).resolve().parents[1] / "pyproject.toml")
    return subprocess.run(
        [sys.executable, "-m", "pathlint", *args],
        cwd=root,
        env=env,
        text=True,
        encoding=encoding,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("args", [[], ["check"], ["."], ["check", "."]])
def test_default_and_legacy_checks(
    workspace: Path, capsys: pytest.CaptureFixture[str], args: list[str]
) -> None:
    write_file(workspace, "sample.py")
    assert cli.main(args) == 1
    output = capsys.readouterr()
    assert output.out == (
        "sample.py:2: PL001 os.path usage: value = os.path.exists('example')\n"
        "Found 1 violation in 1 of 1 files checked.\n"
    )
    assert output.err == ""
    assert f"{chr(27)}[" not in output.out


@pytest.mark.parametrize(
    "args", [["check"], ["check", "--quiet"], ["files"], ["fix"], ["fix", "--dry-run"]]
)
def test_empty_selection_succeeds(workspace: Path, args: list[str]) -> None:
    assert cli.main(args) == 0


def test_quiet_retains_findings(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_file(workspace, "sample.py")
    assert cli.main(["check", "--quiet", "--stats", "--aggressive"]) == 1
    output = capsys.readouterr()
    assert output.out.count("\n") == 1
    assert "PL001" in output.out
    assert output.err == ""


def test_quiet_clean_is_silent(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_file(workspace, "sample.py", "print('clean')\n")
    assert cli.main(["check", "-q"]) == 0
    assert capsys.readouterr() == ("", "")


def test_files_lists_only_deduplicated_selection(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_file(workspace, "a.py", "broken syntax (((")
    write_file(workspace, "src/b.py")
    write_file(workspace, "vendor/c.py")
    write_file(workspace, "backups/d.py")
    assert cli.main(["files", ".", "a.py", "-e", "vendor", "--exclude", "backups"]) == 0
    assert capsys.readouterr() == ("a.py\nsrc/b.py\n", "")


@pytest.mark.parametrize(
    "args, expected_status",
    [
        (["check", "one.py", "--exclude", "vendor", "two.py", "--quiet"], 1),
        (["fix", "one.py", "--dry-run", "two.py", "--quiet"], 1),
        (["files", "one.py", "--exclude", "vendor", "two.py"], 0),
        (["one.py", "--exclude", "vendor", "two.py", "--quiet"], 1),
        (["one.py", "--dry-run", "two.py", "--quiet"], 1),
    ],
)
def test_paths_can_follow_interspersed_options(
    workspace: Path, args: list[str], expected_status: int
) -> None:
    paths = [write_file(workspace, name) for name in ("one.py", "two.py")]
    originals = [path.read_bytes() for path in paths]
    result = invoke(workspace, *args)
    assert result.returncode == expected_status
    assert result.stderr == ""
    assert all(path.name in result.stdout for path in paths)
    assert [path.read_bytes() for path in paths] == originals


@pytest.mark.parametrize("command", ["check", "files", "fix"])
def test_missing_input_does_not_hide_valid_inputs(
    workspace: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    write_file(workspace, "sample.py")
    args = [command, "sample.py", "missing.py"]
    if command == "fix":
        args.append("--dry-run")
    assert cli.main(args) == 2
    output = capsys.readouterr()
    assert "sample.py" in output.out
    assert "missing.py: error: Path does not exist" in output.err
    assert "Traceback" not in output.err


def test_json_reports_findings_syntax_and_missing_inputs(workspace: Path) -> None:
    write_file(workspace, "sample.py")
    write_file(workspace, "broken.py", "if True\n    pass\n")
    result = invoke(workspace, "check", ".", "missing.py", "--output-format", "json", "--stats")
    assert result.returncode == 2
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["diagnostics"] == [
        {
            "path": "sample.py",
            "line": 2,
            "code": "PL001",
            "message": "Use pathlib instead of os.path",
            "source": "value = os.path.exists('example')",
        }
    ]
    assert {entry["path"] for entry in report["errors"]} == {"broken.py", "missing.py"}
    assert report["summary"] == {
        "files_checked": 1,
        "files_with_issues": 1,
        "violations": 1,
        "errors": 2,
    }
    assert report["statistics"] == [{"path": "sample.py", "violations": 1}]


def test_json_clean_and_decode_error(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_file(workspace, "clean.py", "print('clean')\n")
    (workspace / "bad.py").write_bytes(b"\xff\xfe")
    assert cli.main(["check", "--output-format=json"]) == 2
    output = capsys.readouterr()
    assert output.err == ""
    report = json.loads(output.out)
    assert report["diagnostics"] == []
    assert report["errors"][0]["path"] == "bad.py"
    assert report["summary"]["files_checked"] == 1
    assert "statistics" not in report


@pytest.mark.parametrize("flag", ["--quiet", "--aggressive"])
def test_json_rejects_text_only_flags(workspace: Path, flag: str) -> None:
    result = invoke(workspace, "check", "--output-format", "json", flag)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "cannot be combined" in result.stderr


@pytest.mark.parametrize("command", ["fix", "files"])
def test_json_is_check_only(workspace: Path, command: str) -> None:
    result = invoke(workspace, command, "--output-format", "json")
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_statistics_are_sorted_capped_and_use_single_read(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for index in range(7):
        write_file(workspace, f"{index}.py", "import os\n" + "os.path.exists('x')\n" * (index + 1))
    original = cli._lint_file
    reads: list[Path] = []

    def track_read(path: Path) -> list[tuple[int, str]]:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(cli, "_lint_file", track_read)
    assert cli.main(["check", "--stats", "--output-format", "json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [entry["path"] for entry in report["statistics"]] == [
        "6.py",
        "5.py",
        "4.py",
        "3.py",
        "2.py",
    ]
    assert len(reads) == 7
    assert len(set(reads)) == 7


@pytest.mark.parametrize("legacy", [False, True])
def test_fix_dry_run_is_immutable_then_fix_is_idempotent(
    workspace: Path, capsys: pytest.CaptureFixture[str], legacy: bool
) -> None:
    path = write_file(workspace, "sample.py")
    original = path.read_bytes()
    args = ["--dry-run", "sample.py"] if legacy else ["fix", "sample.py", "--dry-run"]
    assert cli.main([*args, "--quiet"]) == 1
    output = capsys.readouterr()
    assert output.out == (
        "--- sample.py\n"
        "+++ sample.py (fixed)\n"
        "@@ -1,2 +1,3 @@\n"
        " import os\n"
        "-value = os.path.exists('example')\n"
        "+from pathlib import Path\n"
        "+value = Path('example').exists()\n"
    )
    assert chr(92) + "033" not in output.out
    assert "Would make" not in output.out
    assert f"{chr(27)}[" not in output.out
    assert output.err == ""
    assert path.read_bytes() == original
    args = ["--fix", "sample.py"] if legacy else ["fix", "sample.py"]
    assert cli.main([*args, "--quiet"]) == 0
    assert "Path('example').exists()" in path.read_text(encoding="utf-8")
    assert capsys.readouterr() == ("", "")
    assert cli.main(["fix", "sample.py", "--dry-run", "--quiet"]) == 0
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("no_color", [False, True])
def test_diff_color_requires_terminal_and_respects_option(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    no_color: bool,
) -> None:
    write_file(workspace, "sample.py")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    args = ["fix", "--dry-run"]
    if no_color:
        args.append("--no-color")
    assert cli.main(args) == 1
    output = capsys.readouterr().out
    assert (chr(27) in output) is not no_color
    assert chr(92) + "033" not in output
    if not no_color:
        assert f"{chr(27)}[91m-value = os.path.exists('example'){chr(27)}[0m\n" in output
        assert f"{chr(27)}[92m+value = Path('example').exists(){chr(27)}[0m\n" in output


@pytest.mark.parametrize("command", ["check", "fix"])
def test_read_errors_have_error_status_and_stderr(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    write_file(workspace, "sample.py")
    original_open = Path.open

    def deny(path: Path, *args: object, **kwargs: object):
        if path.name == "sample.py":
            raise PermissionError("fixture permission denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)
    assert cli.main([command]) == 2
    output = capsys.readouterr()
    assert "sample.py: error: fixture permission denied" in output.err
    assert "Traceback" not in output.err


def test_write_error_returns_error_status(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_file(workspace, "sample.py")
    original = path.read_bytes()
    original_open = Path.open

    def deny_write(path: Path, mode: str = "r", *args: object, **kwargs: object):
        if mode == "w":
            raise PermissionError("fixture write denied")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_write)
    assert cli.main(["fix"]) == 2
    assert "fixture write denied" in capsys.readouterr().err
    assert path.read_bytes() == original


def test_fix_parse_error_retains_original(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_file(workspace, "broken.py", "import os\nos.path.exists(\n")
    original = path.read_bytes()
    assert cli.main(["fix"]) == 2
    assert "broken.py:" in capsys.readouterr().err
    assert path.read_bytes() == original


@pytest.mark.parametrize("prefix", [[], ["prefix.py"], ["check", "prefix.py", "-q"]])
def test_legacy_flags_after_sentinel_are_filenames(
    workspace: Path, capsys: pytest.CaptureFixture[str], prefix: list[str]
) -> None:
    write_file(workspace, "prefix.py", "print('clean')\n")
    write_file(workspace, "--fix", "not Python")
    write_file(workspace, "--dry-run.py")
    assert cli.main([*prefix, "--", "--fix", "--dry-run.py"]) == 1
    assert "--dry-run.py:2: PL001" in capsys.readouterr().out


def test_exclude_option_value_does_not_activate_legacy_fix(workspace: Path) -> None:
    with pytest.raises(SystemExit) as result:
        cli.parse_args(["--exclude", "--fix", "."])
    assert result.value.code == 2
    args = cli.parse_args(["--exclude=--fix", "."])
    assert args.command == "check"
    assert args.exclude == ["--fix"]


def test_legacy_fix_flag_is_not_removed_from_option_value() -> None:
    args = cli.parse_args(["--fix", "--exclude=--fix", "."])
    assert args.command == "fix"
    assert args.exclude == ["--fix"]


@pytest.mark.parametrize("command", [None, "check", "fix", "files"])
def test_help_lists_shared_options(workspace: Path, command: str | None) -> None:
    args = [command, "--help"] if command else ["--help"]
    result = invoke(workspace, *args)
    assert result.returncode == 0
    assert result.stderr == ""
    for text in (
        ("--exclude", "--no-gitignore", "--no-default-excludes")
        if command
        else ("check", "fix", "files", "--version")
    ):
        assert text in result.stdout
    assert "PosixPath" not in result.stdout


def test_version_uses_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "version", lambda name: "9.8.7" if name == "pathlint" else "wrong")
    with pytest.raises(SystemExit) as result:
        cli.main(["--version"])
    assert result.value.code == 0
    assert capsys.readouterr().out.endswith(" 9.8.7\n")


def test_uninstalled_source_version_is_helpful(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unavailable(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(cli, "version", unavailable)
    with pytest.raises(SystemExit) as result:
        cli.main(["--version"])
    assert result.value.code == 0
    assert "unknown (not installed)" in capsys.readouterr().out


@pytest.mark.parametrize("installed", [True, False])
def test_package_version_uses_metadata_or_unknown(
    monkeypatch: pytest.MonkeyPatch, installed: bool
) -> None:
    def lookup(name: str) -> str:
        assert name == "pathlint"
        if not installed:
            raise PackageNotFoundError(name)
        return "9.8.7"

    try:
        with monkeypatch.context() as patch:
            patch.setattr(importlib.metadata, "version", lookup)
            importlib.reload(pathlint)
            from pathlint import __version__

            assert __version__ == ("9.8.7" if installed else "unknown")
    finally:
        importlib.reload(pathlint)


def test_package_version_does_not_need_generated_version_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        with monkeypatch.context() as patch:
            patch.setattr(importlib.metadata, "version", lambda name: "9.8.7")
            patch.setitem(sys.modules, "pathlint._version", None)
            importlib.reload(pathlint)
            assert pathlint.__version__ == "9.8.7"
    finally:
        importlib.reload(pathlint)


def test_cli_and_package_report_same_version(workspace: Path) -> None:
    from pathlint import __version__

    result = invoke(workspace, "--version")
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.split()[1] == __version__


def test_interrupt_returns_conventional_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(*args: object, **kwargs: object):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "discover_files", interrupt)
    assert cli.main(["files"]) == 130


@pytest.mark.parametrize(
    "args",
    [
        ["files", "sample.py"],
        ["check", "sample.py"],
        ["check", "sample.py", "--output-format=json"],
        ["fix", "sample.py", "--dry-run"],
        ["--help"],
        ["check", "--help"],
        ["--version"],
    ],
)
def test_closed_stdout_pipe_returns_one_without_shutdown_error(
    workspace: Path, args: list[str]
) -> None:
    path = write_file(workspace, "sample.py")
    original = path.read_bytes()
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(cli.__file__).parents[1]),
        COV_CORE_CONFIG=str(Path(__file__).resolve().parents[1] / "pyproject.toml"),
    )
    with subprocess.Popen(
        [sys.executable, "-m", "pathlint", *args],
        cwd=workspace,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        assert process.stdout is not None
        assert process.stderr is not None
        process.stdout.close()
        assert process.wait(timeout=10) == 1
        assert process.stderr.read() == b""
    assert path.read_bytes() == original


def test_module_commands_follow_exit_and_stream_contract(workspace: Path) -> None:
    path = write_file(workspace, "sample.py")
    listing = invoke(workspace, "files")
    assert listing.returncode == 0
    assert listing.stdout == "sample.py\n"
    assert listing.stderr == ""
    preview = invoke(workspace, "fix", "--dry-run", "--quiet")
    assert preview.returncode == 1
    assert "+from pathlib import Path" in preview.stdout
    assert preview.stderr == ""
    assert path.read_text(encoding="utf-8") == OFFENDING
    fixing = invoke(workspace, "fix", "--quiet")
    assert fixing.returncode == 0
    assert fixing.stdout == fixing.stderr == ""
    clean = invoke(workspace, "check", "--output-format", "json")
    assert clean.returncode == 0
    assert clean.stderr == ""
    assert json.loads(clean.stdout)["summary"]["violations"] == 0


def test_fix_crlf_preview_has_clean_lines_and_preserves_file_newlines(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = workspace / "sample.py"
    original = OFFENDING.replace("\n", "\r\n").encode()
    path.write_bytes(original)
    assert cli.main(["fix", "--dry-run", "--quiet"]) == 1
    preview = capsys.readouterr().out
    assert "\r" not in preview
    assert "\n\n" not in preview
    assert preview.splitlines() == [
        "--- sample.py",
        "+++ sample.py (fixed)",
        "@@ -1,2 +1,3 @@",
        " import os",
        "-value = os.path.exists('example')",
        "+from pathlib import Path",
        "+value = Path('example').exists()",
    ]
    assert path.read_bytes() == original
    assert cli.main(["fix", "--quiet"]) == 0
    updated = path.read_bytes()
    assert b"\r\n" in updated
    assert b"\n" not in updated.replace(b"\r\n", b"")


@pytest.mark.parametrize("suffix, markers", [("", 2), ("\n", 0), ("\n# tail", 1)])
def test_preview_preserves_final_newline_information(
    workspace: Path, suffix: str, markers: int
) -> None:
    if shutil.which("git") is None:
        pytest.skip("Git is required to apply preview patches")
    source = "import os\nvalue = os.path.exists('example')" + suffix
    path = write_file(workspace, "sample.py", source)
    original = path.read_bytes()
    preview = invoke(workspace, "fix", "sample.py", "--dry-run", "--quiet")
    assert preview.returncode == 1
    assert preview.stderr == ""
    assert path.read_bytes() == original
    assert preview.stdout.count("\\ No newline at end of file\n") == markers
    patch = preview.stdout.replace("+++ sample.py (fixed)\n", "+++ sample.py\n", 1)
    applied = subprocess.run(
        ["git", "apply", "--no-index", "-p0", "-"],
        cwd=workspace,
        input=patch.encode(),
        capture_output=True,
        check=False,
    )
    assert applied.returncode == 0, applied.stderr
    expected = "import os\nfrom pathlib import Path\nvalue = Path('example').exists()" + suffix
    assert path.read_bytes() == expected.encode()
    assert invoke(workspace, "fix", "sample.py", "--dry-run", "--quiet").returncode == 0


@pytest.mark.parametrize("clean", [False, True])
def test_cp1252_preserves_clean_and_aggressive_status(workspace: Path, clean: bool) -> None:
    write_file(workspace, "sample.py", "print('clean')\n" if clean else OFFENDING)
    result = invoke(workspace, "check", "sample.py", "--aggressive", encoding="cp1252")
    assert result.returncode == (0 if clean else 1)
    assert result.stderr == ""
    assert result.stdout.isascii()
    if clean:
        assert result.stdout == "Checked 1 files; no os.path usage found.\n"
    else:
        assert "Found 1 violation in 1 of 1 files checked." in result.stdout
        assert "WARNING: ARE YOU KIDDING ME? USE PATHLIB!" in result.stdout


@pytest.mark.parametrize("encoding", ["cp1252", "utf-8"])
def test_output_encoding_preserves_unicode_data_and_command_status(
    workspace: Path, encoding: str
) -> None:
    name = "café_🐍.py"
    source = "import os\nvalue = os.path.exists('café/🐍')\n"
    path = write_file(workspace, name, source)
    original = path.read_bytes()
    displayed_name = name.encode(encoding, errors="backslashreplace").decode(encoding)
    displayed_source = (
        source.splitlines()[1].encode(encoding, errors="backslashreplace").decode(encoding)
    )

    check = invoke(workspace, "check", name, "--stats", encoding=encoding)
    assert check.returncode == 1
    assert check.stderr == ""
    assert f"{displayed_name}:2: PL001 os.path usage: {displayed_source}" in check.stdout
    assert f"1 - {displayed_name}" in check.stdout

    machine = invoke(workspace, "check", name, "--output-format=json", encoding=encoding)
    assert machine.returncode == 1
    assert machine.stderr == ""
    report = json.loads(machine.stdout)
    assert report["diagnostics"][0]["path"] == name
    assert report["diagnostics"][0]["source"] == source.splitlines()[1]

    listing = invoke(workspace, "files", name, encoding=encoding)
    assert listing.returncode == 0
    assert listing.stderr == ""
    assert listing.stdout == displayed_name + "\n"

    preview = invoke(workspace, "fix", name, "--dry-run", encoding=encoding)
    assert preview.returncode == 1
    assert preview.stderr == ""
    assert f"--- {displayed_name}\n" in preview.stdout
    assert f"-{displayed_source}\n" in preview.stdout
    assert path.read_bytes() == original

    fixing = invoke(workspace, "fix", name, encoding=encoding)
    assert fixing.returncode == 0
    assert fixing.stderr == ""
    assert f"Fixed {displayed_name}:" in fixing.stdout
    assert "café/🐍".encode() in path.read_bytes()
    assert invoke(workspace, "check", name, encoding=encoding).returncode == 0


@pytest.mark.parametrize("encoding", ["cp1252", "utf-8"])
def test_unicode_missing_input_keeps_error_status(workspace: Path, encoding: str) -> None:
    name = "missing_🐍.py"
    result = invoke(workspace, "check", name, encoding=encoding)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    displayed_name = name.encode(encoding, errors="backslashreplace").decode(encoding)
    assert f"{displayed_name}: error: Path does not exist" in result.stderr
