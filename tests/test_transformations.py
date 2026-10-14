"""Behavioral regressions for AST-backed, source-preserving transformations."""

import ast
import ntpath
import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from textwrap import indent
from types import ModuleType

import pytest

from pathlint.autofix import fix_content
from pathlint.linter import OSPathDetector, _lint_file


def run(source: str) -> object:
    """Execute a fixture and return its result."""
    namespace: dict[str, object] = {}
    exec(compile(source, "<fixture>", "exec"), namespace)
    return namespace["result"]


@pytest.mark.parametrize(
    ("imports", "expression"),
    [
        ("import os", "os.path.isabs(FILE)"),
        ("import os", "os.path.isabs('C:/directory/file')"),
        ("import os as operating_system", "operating_system.path.isabs(FILE)"),
        ("import os.path as paths", "paths.isabs(FILE)"),
        ("from os import path as paths", "paths.isabs(FILE)"),
        ("from os.path import isabs as present", "present(FILE)"),
        ("from os.path import isabs", "isabs(FILE)"),
        ("import os", "os.path.basename(FILE)"),
        ("import os", "os.path.dirname(FILE)"),
        ("import os", "os.path.expanduser('directory')"),
        ("import os", "os.path.join('directory', 'file')"),
        ("import os", "os.path.basename(os.path.join('directory', 'file'))"),
        ("import pathlib\nimport os", "os.path.isabs(FILE)"),
        ("import pathlib as paths\nimport os", "os.path.isabs(FILE)"),
        ("from pathlib import Path as P\nimport os", "os.path.isabs(FILE)"),
        ("import os.path as paths", "(paths.basename)('directory/file')"),
        ("import os", "((os.path.isabs))(FILE)"),
        ("from os.path import isabs as present", "(present)(FILE)"),
        ("import os", "(os.path.basename)((os.path.join)('directory', 'file'))"),
        ("import os", "(\n    (os.path.basename)  # callable (comment\n    ('directory/file')\n)"),
    ],
)
def test_supported_calls_preserve_results(imports: str, expression: str) -> None:
    original = imports + "\nresult = " + expression.replace("FILE", "'directory/file'") + "\n"
    updated, count = fix_content(original)
    assert count > 0
    assert run(updated) == run(original)
    detector = OSPathDetector(updated.splitlines())
    detector.visit(ast.parse(updated))
    assert not detector.offenses
    assert fix_content(updated) == (updated, 0)


@pytest.mark.parametrize(
    "expression",
    [
        "os.path.abspath('directory/../file')",
        "os.path.splitext('directory/file.tar.gz')",
        "os.path.sep",
        "os.path.pathsep",
        "os.path.isabs(s='file')",
        "os.path.join('directory', *parts)",
        "os.path.isabs(b'file')",
        "os.path.basename('directory/')",
        "os.path.basename('.')",
        "os.path.dirname('file')",
        "os.path.dirname('directory/')",
        "os.path.join('directory', '')",
        "os.path.join('directory//subdirectory', 'file')",
    ],
)
def test_unsupported_or_known_inexact_forms_stay_unchanged(expression: str) -> None:
    original = f"import os\nresult = {expression}\n"
    assert fix_content(original) == (original, 0)


@pytest.mark.parametrize("value", ["/file", "\\file", "//server"])
def test_windows_rooted_isabs_literals_stay_unchanged(value: str) -> None:
    """Keep roots whose Windows interpretation differs on supported Python versions."""
    original = f"import os\nresult = os.path.isabs({value!r})\n"
    assert fix_content(original) == (original, 0)
    if sys.version_info < (3, 13):
        assert ntpath.isabs(value)
        assert not PureWindowsPath(value).is_absolute()
    assert run(fix_content(original)[0]) == run(original)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("join", ("a/b", "c")),
        ("join", ("a", "b/c")),
        ("join", (r"a\\b", "c")),
        ("join", (r"a\.\b", "c")),
        ("join", (r".\a", "b")),
        ("join", ("C:.", "b")),
        ("join", ("C:a", "C:")),
        ("join", (r"C:\a", "C:")),
        ("basename", ("C:.",)),
        ("basename", ("c:.",)),
        ("basename", ("Z:.",)),
        ("dirname", ("a/b/c",)),
        ("dirname", ("/file",)),
        ("dirname", ("C:/file",)),
        ("dirname", ("\\/file",)),
        ("dirname", ("a/b\\c",)),
        ("dirname", ("a\\\\b/c",)),
        ("dirname", ("a\\.\\b/c",)),
        ("dirname", ("C:./a",)),
        ("dirname", (r"\./a",)),
        ("expanduser", ("a/b",)),
        ("expanduser", (r"a\\b",)),
        ("expanduser", (r"a\.\b",)),
        ("expanduser", (r".\a",)),
        ("expanduser", ("C:.",)),
    ],
)
def test_windows_separator_spellings_stay_unchanged(
    monkeypatch: pytest.MonkeyPatch, method: str, args: tuple[str, ...]
) -> None:
    """Keep literal spellings whose Windows string results pathlib normalizes."""
    from types import SimpleNamespace

    from pathlint import autofix

    original_result = getattr(ntpath, method)(*args)
    path = PureWindowsPath(*args)
    replacement_result = (
        path.name if method == "basename" else str(path.parent if method == "dirname" else path)
    )
    assert original_result != replacement_result
    original = (
        f"import os.path as osp\nresult = osp.{method}({', '.join(repr(arg) for arg in args)})\n"
    )
    monkeypatch.setattr(autofix, "sys", SimpleNamespace(platform="win32"))
    assert fix_content(original) == (original, 0)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("join", ("a", "b")),
        ("join", (r"a\b", "c")),
        ("join", ("C:", "b")),
        ("join", ("C:a", "D:")),
        ("join", ("C:a", "C:b")),
        ("basename", ("C:",)),
        ("basename", ("C:file",)),
        ("basename", ("C:..",)),
        ("basename", ("C:/file",)),
        ("basename", (r"C:\file",)),
        ("basename", (r"a\\b",)),
        ("basename", (r"a\.\b",)),
        ("basename", ("a/b",)),
        ("dirname", ("a/b",)),
        ("dirname", ("a\\b/c",)),
        ("expanduser", ("a",)),
        ("expanduser", (r"a\b",)),
        ("expanduser", ("C:",)),
    ],
)
def test_windows_exact_separator_spellings_remain_supported(
    monkeypatch: pytest.MonkeyPatch, method: str, args: tuple[str, ...]
) -> None:
    """Retain supported Windows literals when their string results are exact."""
    from types import SimpleNamespace

    from pathlint import autofix

    expected = getattr(ntpath, method)(*args)
    path = PureWindowsPath(*args)
    replacement_result = (
        path.name if method == "basename" else str(path.parent if method == "dirname" else path)
    )
    assert expected == replacement_result
    original = (
        f"import os.path as osp\nresult = osp.{method}({', '.join(repr(arg) for arg in args)})\n"
    )
    monkeypatch.setattr(autofix, "sys", SimpleNamespace(platform="win32"))
    updated, count = fix_content(original)
    assert count > 0
    assert updated != original


@pytest.mark.parametrize("method", ["exists", "isfile", "isdir"])
@pytest.mark.parametrize("kind", ["file", "directory", "missing", "overlong"])
@pytest.mark.parametrize(
    ("imports", "call"),
    [
        ("import os", "os.path.{method}"),
        ("import os.path as paths", "paths.{method}"),
        ("from os import path as paths", "paths.{method}"),
        ("from os.path import {method} as predicate", "predicate"),
    ],
)
def test_filesystem_predicates_preserve_source_and_results(
    tmp_path: Path, method: str, kind: str, imports: str, call: str
) -> None:
    """Keep predicate imports and calls, including filesystem errors returning False."""
    target = tmp_path / ("a" * 300 if kind == "overlong" else kind)
    if kind == "file":
        target.write_text("content", encoding="utf-8")
    elif kind == "directory":
        target.mkdir()
    original = (
        imports.format(method=method)
        + f"\nresult = {call.format(method=method)}({str(target)!r})\n"
    )
    assert fix_content(original) == (original, 0)
    expected = kind in {"file", "directory"} and (
        method == "exists" or (method == "isfile") == (kind == "file")
    )
    assert run(original) is expected
    detector = OSPathDetector(original.splitlines())
    detector.visit(ast.parse(original))
    assert detector.offenses


def test_supported_fix_retains_predicate_imports(tmp_path: Path) -> None:
    """Migrate a supported call while preserving neighboring filesystem predicates."""
    target = tmp_path / "file"
    target.write_text("content", encoding="utf-8")
    original = (
        "from os.path import exists, isfile, isdir, basename\n"
        f"result = (exists({str(target)!r}), isfile({str(target)!r}), "
        f"isdir({str(tmp_path)!r}), basename('directory/file'))\n"
    )
    updated, count = fix_content(original)
    assert count == 2
    assert "from os.path import exists, isfile, isdir\n" in updated
    assert "Path('directory/file').name" in updated
    assert run(updated) == run(original) == (True, True, True, "file")
    assert fix_content(updated) == (updated, 0)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import os\nfrom pathlib import Path\nfrom zipfile import *\n"
            "result = os.path.basename('directory/file')\n",
            "file",
        ),
        (
            "import os\nflag = True\nfrom zipfile import *\n"
            "result = os.path.basename('directory/file')\n",
            "file",
        ),
        (
            "import os\nfrom pathlib import Path\n"
            "def check():\n    return os.path.basename('directory/file')\n"
            "from zipfile import *\nresult = check()\n",
            "file",
        ),
        (
            "import os\nfrom pathlib import Path\n"
            "checks = (os.path.basename('directory/file') for _ in [0])\n"
            "from zipfile import *\nresult = next(checks)\n",
            "file",
        ),
        (
            "import os\nif True:\n    from zipfile import *\n"
            "result = os.path.basename('directory/file')\n",
            "file",
        ),
        (
            "import os as operating_system\nPath = None\nfrom wildcard_exports import *\n"
            "result = operating_system.path.basename('directory/file')\n",
            "file",
        ),
        (
            "import os\nfrom wildcard_exports import *\n"
            "result = os.path.basename('directory/file')\n",
            "custom",
        ),
        (
            "from os.path import basename\nfrom zipfile import *\nresult = 'file'\n",
            "file",
        ),
    ],
)
def test_wildcard_imports_leave_source_unchanged(
    tmp_path: Path, source: str, expected: str
) -> None:
    (tmp_path / "wildcard_exports.py").write_text(
        "from types import SimpleNamespace\n"
        "PathlintPath = lambda value: None\n"
        "os = SimpleNamespace(path=SimpleNamespace(basename=lambda value: 'custom'))\n",
        encoding="utf-8",
    )
    updated, count = fix_content(source)
    assert count == 0
    assert updated == source
    environment = os.environ.copy()
    if environment.get("COV_CORE_SOURCE"):
        environment["COV_CORE_CONFIG"] = str(Path(__file__).resolve().parents[1] / "pyproject.toml")
    for content in (source, updated):
        result = subprocess.run(
            [sys.executable, "-c", content + "\nprint(result)\n"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected + "\n"
        assert result.stderr == ""


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Type parameter syntax requires Python 3.12")
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "class C[Path]:\n    value = os.path.basename('a/b')\nresult = C.value\n",
            "b",
        ),
        (
            "def f[Path]():\n    return os.path.basename('a/b')\nresult = f()\n",
            "b",
        ),
        ("type Alias[Path] = os.path.basename('a/b')\nresult = Alias.__value__\n", "b"),
        (
            "type Alias = os.path.join('a', 'b')\n"
            "str = lambda value: 'custom'\nresult = Alias.__value__\n",
            str(Path("a") / "b"),
        ),
        (
            "type Alias = os.path.basename('a/b')\n"
            "os = __import__('types').SimpleNamespace(\n"
            "    path=__import__('types').SimpleNamespace(basename=lambda value: 'custom'))\n"
            "result = Alias.__value__\n",
            "custom",
        ),
    ],
)
def test_type_parameter_scopes_and_lazy_aliases_are_not_rewritten(body: str, expected: str) -> None:
    """Preserve constructs whose annotation scopes need separate binding analysis."""
    original = "import os\nfrom pathlib import Path\n" + body
    updated, count = fix_content(original)
    assert (updated, count) == (original, 0)
    assert run(updated) == run(original) == expected
    detector = OSPathDetector(original.splitlines())
    detector.visit(ast.parse(original))
    assert detector.offenses


@pytest.mark.parametrize(
    "expression",
    [
        "os.path.exists('')",
        "os.path.isdir('')",
        "os.path.isfile('')",
        "os.path.exists(0)",
        "os.path.isfile(0)",
        "os.path.isdir(0)",
        "os.path.exists(-1)",
        "os.path.expanduser('')",
        "os.path.expanduser('directory/')",
        "os.path.expanduser('~__pathlint_nonexistent_user__/file')",
        "os.path.expanduser('./file')",
        "os.path.join('./file')",
        "os.path.join('directory', './file')",
        "os.path.dirname('./directory/file')",
        r"os.path.expanduser('~\\file')",
        r"os.path.expanduser('~\\directory\\file')",
        r"os.path.dirname('directory\\file')",
    ],
)
def test_inexact_literal_calls_preserve_results(expression: str) -> None:
    source = f"import os\nresult = {expression}\n"
    updated, count = fix_content(source)
    assert count == 0
    assert updated == source
    assert run(updated) == run(source)


@pytest.mark.parametrize("method", ["exists", "isfile"])
def test_file_with_trailing_separator_is_not_normalized(tmp_path: Path, method: str) -> None:
    target = tmp_path / "file"
    target.write_text("content", encoding="utf-8")
    source = f"import os\nresult = os.path.{method}({str(target) + '/'!r})\n"
    updated, count = fix_content(source)
    assert count == 0
    assert updated == source
    assert run(updated) == run(source) is False


def test_abspath_is_not_replaced_by_symlink_resolution(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    original = f"import os\nresult = os.path.abspath({str(alias)!r})\n"
    updated, count = fix_content(original)
    assert count == 0
    assert run(updated) == str(alias)
    assert alias.resolve() != alias


def test_strings_comments_and_fstring_text_survive() -> None:
    original = (
        '"""os.path.isabs("docstring")"""\n'
        "import os.path\n"
        "# os.path.isabs('comment')\n"
        "literal = \"os.path.isabs('literal')\"\n"
        "result = f\"os.path.isabs('text'): {os.path.isabs('directory/file')}\"\n"
    )
    updated, count = fix_content(original)
    assert count > 0
    for text in (
        '"""os.path.isabs("docstring")"""',
        "# os.path.isabs('comment')",
        "literal = \"os.path.isabs('literal')\"",
        "os.path.isabs('text'): ",
    ):
        assert text in updated
    assert run(updated) == run(original)


@pytest.mark.parametrize(
    "expression",
    [
        '''f"{p.basename('a/b')=}"''',
        '''f"{p.basename('a/b') = !r}"''',
        '''f"{(p.basename('a/b'))=}"''',
        '''f"{p.basename(p.join('a', 'b'))=}"''',
        '''f"""{f"{p.basename('a/b')}"=}"""''',
    ],
)
def test_debug_fstring_labels_preserve_original_expressions(expression: str) -> None:
    """Keep debug field labels, including calls nested within their expressions."""
    original = f"import os.path as p\nresult = {expression}\n"
    updated, count = fix_content(original)
    assert count == 0
    assert updated == original
    assert run(updated) == run(original)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="F-string comments require Python 3.12")
def test_debug_fstring_comment_before_equals_is_preserved() -> None:
    """Recognize a debug marker after a multiline grouping and comment."""
    original = (
        "import os.path as p\n" '''result = f"""{(p.basename('a/b') # keep comment\n)=}"""\n'''
    )
    updated, count = fix_content(original)
    assert count == 0
    assert updated == original
    assert run(updated) == run(original)


def test_debug_fstring_deferred_lambda_retains_its_label() -> None:
    """Preserve nested deferred calls when their source contributes to a debug label."""
    original = (
        "import os.path as p\n"
        """label = f"{(lambda: p.basename('a/b'))=}"\n"""
        "result = label.split('=', 1)[0]\n"
    )
    updated, count = fix_content(original)
    assert count == 0
    assert updated == original
    assert run(updated) == run(original) == "(lambda: p.basename('a/b'))"


@pytest.mark.parametrize(
    "expression",
    [
        '''f"value={p.basename('a/b')}"''',
        '''f"{p.basename('a/b')!r}"''',
        '''f"{p.basename('a/b') == 'b'}"''',
        '''f"{p.basename('a/b')=:{len(p.basename('c/d'))}}"''',
    ],
)
def test_ordinary_fstring_fields_and_debug_format_specs_are_rewritten(expression: str) -> None:
    """Migrate ordinary fields and dynamic format specs while retaining debug labels."""
    original = f"import os.path as p\nresult = {expression}\n"
    updated, count = fix_content(original)
    assert count > 0
    assert "Path(" in updated
    assert run(updated) == run(original)
    assert fix_content(updated) == (updated, 0)


def test_debug_fstring_walrus_still_updates_bindings() -> None:
    """Analyze assignments inside preserved debug fields before later calls."""
    original = (
        "import os\nfrom types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(basename=str.upper))\n"
        """label = f"{(os := custom)=}"\n"""
        "result = os.path.basename('a/b')\n"
    )
    updated, count = fix_content(original)
    assert count == 0
    assert updated == original
    assert run(updated) == run(original) == "A/B"


@pytest.mark.parametrize(
    "separator", ["\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]
)
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_unicode_offsets_and_multiline_comments(
    tmp_path: Path, separator: str, newline: str
) -> None:
    original = (
        f'"""Module {separator} documentation."""\n'
        "import os.path\n"
        f"label = '🐍{separator}';\fresult = os.path.isabs(\n    'directory/file',  # keep this comment\n)\n"
    ).replace("\n", newline)
    updated, count = fix_content(original)
    assert count > 0
    assert f'"""Module {separator} documentation."""' in updated
    assert f"label = '🐍{separator}';" in updated
    assert "# keep this comment" in updated
    assert updated.count(newline) == original.count(newline)
    assert "\n" not in updated.replace(newline, "")
    assert run(updated) == run(original)
    target = tmp_path / "unicode.py"
    with target.open("w", encoding="utf-8", newline="") as stream:
        stream.write(original)
    assert _lint_file(target) == [
        (2, "import os.path"),
        (3, f"label = '🐍{separator}';\fresult = os.path.isabs("),
    ]


@pytest.mark.parametrize(
    ("imports", "callee", "argument", "changes", "expected"),
    [
        ("import os.path as osp", "osp.isabs", "'directory/file'", True, False),
        (
            "import os.path as osp",
            "osp # inside callable 🐍\n        .isabs",
            "'directory/file'",
            False,
            False,
        ),
        (
            "import os as osp",
            "osp # module comment\n        .path # path comment\n        .basename",
            "'a/b'",
            False,
            "b",
        ),
        (
            "import os as osp",
            "osp # module comment\n        .path # path comment\n        .basename",
            "osp.path.basename('a/b')",
            True,
            "b",
        ),
    ],
)
def test_parenthesized_callable_comments_are_preserved(
    imports: str, callee: str, argument: str, changes: bool, expected: object
) -> None:
    original = (
        f"{imports}\n"
        "result = (\n"
        "    (  # before callable 🐍 (\n"
        f"        {callee}\n"
        "    )  # after callable (\n"
        f"    ({argument})\n"
        ")\n"
    )
    updated, count = fix_content(original)
    assert bool(count) is changes
    if not changes:
        assert updated == original
    for line in original.splitlines():
        if "#" in line:
            assert line[line.index("#") :] in updated
    if "#" in callee:
        assert callee in updated
    assert run(updated) == run(original) == expected
    assert fix_content(updated) == (updated, 0)


def test_import_follows_docstring_and_future_import() -> None:
    original = (
        '#!/usr/bin/env python3\n# coding: utf-8\n"""Module documentation.\n@literal\n"""\n'
        "from __future__ import annotations\n"
        "import os.path\n"
        "result = os.path.isabs('directory/file')\n"
    )
    updated, _ = fix_content(original)
    assert updated.startswith(
        '#!/usr/bin/env python3\n# coding: utf-8\n"""Module documentation.\n@literal\n"""\n'
    )
    assert updated.index("from __future__") < updated.index("from pathlib")
    assert run(updated) == run(original)


@pytest.mark.parametrize(
    "source",
    [
        "@lambda fn: fn\ndef check():\n    from os.path import basename\n"
        "    return basename('a/b')\nresult = check()\n",
        "@(\n    lambda fn: fn\n)\ndef check():\n    from os.path import basename\n"
        "    return basename('a/b')\nresult = check()\n",
        "from os.path import basename\n@lambda fn: fn\ndef check():\n"
        "    return basename('a/b')\nresult = check()\n",
        "from os.path import basename\n@lambda cls: cls\nclass Paths:\n"
        "    value = basename('a/b')\nresult = Paths.value\n",
        "from os.path import basename\n@(\n    lambda cls: cls\n)\nclass Paths:\n"
        "    value = basename('a/b')\nresult = Paths.value\n",
        "import asyncio\nfrom os.path import basename\n@lambda fn: fn\n@(\n    lambda fn: fn\n)\n"
        "async def check():\n    return basename('a/b')\n"
        "result = asyncio.run(check())\n",
    ],
)
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_generated_import_precedes_first_decorator(source: str, newline: str) -> None:
    """Preserve executable decorated definitions after inserting a Path import."""
    header = "#!/usr/bin/env python3\n# coding: utf-8\n"
    original = (header + source).replace("\n", newline)
    updated, count = fix_content(original)
    assert count > 0
    assert updated.startswith(header.replace("\n", newline))
    assert updated.index("from pathlib") < updated.index("@")
    assert run(updated) == run(original) == "b"
    assert fix_content(updated) == (updated, 0)


def test_cli_persists_valid_decorated_fix(tmp_path: Path) -> None:
    """Execute the persisted CLI fix for a decorated first definition."""
    source = (
        "from os.path import basename\n@lambda fn: fn\ndef check():\n"
        "    return basename('a/b')\nresult = check()\n"
    )
    target = tmp_path / "decorated.py"
    target.write_text(source, encoding="utf-8")
    process = subprocess.run(
        [sys.executable, "-m", "pathlint", "fix", str(target)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    persisted = target.read_text(encoding="utf-8")
    assert persisted == fix_content(source)[0]
    assert run(persisted) == run(source) == "b"


def test_partial_named_import_keeps_unsupported_function() -> None:
    original = (
        "from os.path import isabs as present, splitext\n"
        "result = (present('directory/file'), splitext('dir/file.ext'))\n"
    )
    updated, count = fix_content(original)
    assert count == 2
    assert "from os.path import splitext" in updated
    assert run(updated) == run(original)


def test_wildcard_import_is_not_deleted() -> None:
    original = "from os.path import *\nresult = isabs('file')\n"
    assert fix_content(original) == (original, 0)


def test_multiline_import_comments_are_preserved() -> None:
    original = (
        "from os.path import (\n    isabs,  # important comment\n)\n"
        "result = isabs('directory/file')\n"
    )
    updated, count = fix_content(original)
    assert count == 1
    assert "# important comment" in updated
    assert run(updated) == run(original)


@pytest.mark.parametrize(
    "preamble",
    [
        "class Path:\n    pass\n",
        "Path = object()\nPathlintPath = object()\n",
        "from pathlib import Path\n",
    ],
)
def test_constructor_does_not_collide_with_local_names(preamble: str) -> None:
    original = (
        preamble + "import os\n"
        "def check(Path):\n"
        "    return os.path.isabs('directory/file')\n"
        "result = check(object())\n"
    )
    updated, count = fix_content(original)
    assert count > 0
    assert run(updated) == run(original)
    assert "from pathlib import Path as PathlintPath" in updated


@pytest.mark.parametrize(
    "source",
    [
        "def check():\n    return os.path.isabs('missing')\nimport os\nresult = check()\n",
        "def outer():\n    def inner():\n        return p.isabs('missing')\n    from os import path as p\n    return inner()\nresult = outer()\n",
    ],
)
def test_imports_defined_after_function_are_not_rewritten(source: str) -> None:
    assert fix_content(source) == (source, 0)
    assert run(source) is False


@pytest.mark.parametrize(
    "body",
    [
        "import os\n"
        "def check():\n    return os.path.isabs('file')\n"
        "os = SimpleNamespace(path=custom)\nresult = check()\n",
        "import os.path as osp\n"
        "def check():\n    return osp.isabs('file')\n"
        "osp = custom\nresult = check()\n",
        "osp = custom\n"
        "def check():\n    return osp.isabs('file')\n"
        "result = check()\nimport os.path as osp\n",
        "import os.path as osp\n"
        "def check():\n    return osp.isabs('file')\n"
        "osp = custom\nresult = check()\nimport os.path as osp\n",
        "def outer():\n"
        "    osp = custom\n"
        "    def check():\n        return osp.isabs('file')\n"
        "    result = check()\n    import os.path as osp\n    return result\n"
        "result = outer()\n",
        "osp = custom\n"
        "check = lambda: osp.isabs('file')\n"
        "result = check()\nimport os.path as osp\n",
        "osp = custom\n"
        "class Check:\n    def check(self):\n        return osp.isabs('file')\n"
        "result = Check().check()\nimport os.path as osp\n",
        "osp = custom\n"
        "def invoke(function):\n    global result\n    result = function()\n    return function\n"
        "@invoke\ndef check():\n    return osp.isabs('file')\n"
        "import os.path as osp\n",
        "import os.path as osp\n"
        "def writer():\n    global osp\n    osp = custom\n"
        "def check():\n    return osp.isabs('file')\n"
        "writer()\nresult = check()\n",
        "def outer():\n    import os.path as osp\n"
        "    def writer():\n        nonlocal osp\n        osp = custom\n"
        "    def check():\n        return osp.isabs('file')\n"
        "    writer()\n    return check()\n"
        "result = outer()\n",
        "def outer():\n    import os.path as osp\n"
        "    def writer():\n        nonlocal osp\n        osp = custom\n"
        "    writer()\n    return osp.isabs('file')\n"
        "result = outer()\n",
        "import os.path as osp\n"
        "checks = (osp.isabs('file') for _ in [0])\n"
        "osp = custom\nresult = next(checks)\n",
        "import os.path as osp\n"
        "checks = ('custom' for _ in [0] if osp.isabs('file') == 'custom')\n"
        "osp = custom\nresult = next(checks)\n",
        "import os.path as osp\n"
        "checks = (value for _ in [0] for value in [osp.isabs('file')])\n"
        "osp = custom\nresult = next(checks)\n",
    ],
)
def test_reassigned_enclosing_aliases_are_not_rewritten(body: str) -> None:
    source = (
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(isabs=lambda value: 'custom')\n" + body
    )
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source) == "custom"


@pytest.mark.parametrize(
    "writer",
    [
        "def configure():\n    global osp\n    osp = ntpath\nconfigure()",
        "def configure():\n    global osp\n    import ntpath as osp\nconfigure()",
        "def configure():\n    global osp\n    from ntpath import basename as osp\nconfigure()",
        "def outer():\n"
        "    def configure():\n        global osp\n        osp = ntpath\n"
        "    configure()\nouter()",
        "def outer():\n    class Configure:\n        global osp\n        osp = ntpath\nouter()",
    ],
)
def test_explicit_global_writes_prevent_immediate_alias_fixes(writer: str) -> None:
    if "basename as osp" in writer:
        imported = "from os.path import basename as osp"
        expression = "osp('a\\\\b')"
    else:
        imported = "import os.path as osp"
        expression = "osp.basename('a\\\\b')"
    source = f"{imported}\nimport ntpath\n{writer}\nresult = {expression}\n"
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source) == "b"


def test_explicit_global_deletion_preserves_immediate_name_error() -> None:
    source = (
        "import os.path as osp\n"
        "def configure():\n    global osp\n    del osp\nconfigure()\n"
        "try:\n    result = osp.basename('a/b')\n"
        "except NameError:\n    result = 'missing'\n"
    )
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source) == "missing"


def test_explicit_global_write_prevents_immediate_string_conversion() -> None:
    source = (
        "import os.path as osp\n"
        "def configure():\n    global str\n    from builtins import repr as str\n"
        "configure()\nresult = osp.join('a', 'b')\n"
    )
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source) == str(Path("a") / "b")


@pytest.mark.parametrize("binding", ["P = ntpath", "import ntpath as P", "del P"])
def test_explicit_global_write_prevents_constructor_reuse(binding: str) -> None:
    source = (
        "from pathlib import Path as P\nimport os.path as osp\nimport ntpath\n"
        f"def configure():\n    global P\n    {binding}\n"
        "configure()\nresult = osp.basename('a/b')\n"
    )
    updated, count = fix_content(source)
    assert count > 0
    assert "result = P(" not in updated
    assert run(updated) == run(source) == "b"


def test_read_only_global_declarations_remain_rewritable() -> None:
    source = (
        "import os.path as osp\n"
        "def check():\n    global osp\n    return osp.basename('a/b')\n"
        "result = check()\n"
    )
    updated, count = fix_content(source)
    assert count > 0
    assert run(updated) == run(source) == "b"


@pytest.mark.parametrize(
    ("definition", "invocation"),
    [
        ("def check():\n    return osp.join('directory', 'file')\n", "check()"),
        ("checks = (osp.join('directory', 'file') for _ in [0])\n", "next(checks)"),
    ],
)
@pytest.mark.parametrize(
    "binding",
    [
        "str = lambda value: 'custom'",
        "from builtins import repr as str",
        "import builtins as str",
        "def writer():\n    global str\n    from builtins import repr as str\nwriter()",
    ],
)
def test_deferred_string_conversion_respects_later_builtin_shadowing(
    binding: str, definition: str, invocation: str
) -> None:
    source = f"import os.path as osp\n{definition}{binding}\nresult = {invocation}\n"
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source) == str(Path("directory") / "file")


def test_deferred_alias_with_conflicting_import_origins_is_not_rewritten() -> None:
    source = (
        "import os.path as osp\n"
        "def check():\n    return osp.basename('directory\\\\file')\n"
        "import ntpath as osp\n"
        "result = check()\n"
        "import os.path as osp\n"
    )
    assert fix_content(source) == (source, 0)
    assert run(source) == "file"


@pytest.mark.parametrize(
    "source",
    [
        "import os\ndef check():\n    return os.path.isabs('missing')\nresult = check()\n",
        "def check():\n    import os\n    return os.path.isabs('missing')\nresult = check()\n",
        "from os.path import isabs\ndef check():\n    return isabs('missing')\nresult = check()\n",
        "import os\n"
        "def outer():\n    from pathlib import Path\n"
        "    def writer():\n        nonlocal Path\n        Path = lambda value: None\n"
        "    writer()\n    return os.path.isabs('missing')\n"
        "result = outer()\n",
        "import os.path as osp\n"
        "checks = (osp.isabs('missing') for _ in [0])\nresult = next(checks)\n",
        "import os.path as osp\n"
        "checks = (value for value in [osp.isabs('missing')])\n"
        "osp = None\nresult = next(checks)\n",
        "import os.path as osp\ndef check():\n    return osp.isabs('missing')\nresult = check()\n",
        "import os.path as osp\ncheck = lambda: osp.isabs('missing')\nresult = check()\n",
        "def outer():\n    import os.path as osp\n"
        "    def check():\n        return osp.isabs('missing')\n"
        "    return check()\n"
        "result = outer()\n",
        "from pathlib import Path as P\n"
        "import os.path as osp\n"
        "def check():\n    return osp.isabs('missing')\n"
        "P = lambda value: 'custom'\n"
        "result = check()\n",
    ],
)
def test_stable_deferred_imports_remain_rewritable(source: str) -> None:
    updated, count = fix_content(source)
    assert count > 0
    assert run(updated) == run(source)
    assert "return P(" not in updated


def test_deferred_relative_import_binding_is_not_rewritten(tmp_path: Path) -> None:
    source = (
        "import os.path as osp\n"
        "def check():\n    return osp.isabs('missing')\n"
        "from . import custom as osp\n"
        "result = check()\n"
        "import os.path as osp\n"
        "print(result)\n"
    )
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    package = tmp_path / "example"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "custom.py").write_text("def isabs(value): return 'custom'\n", encoding="utf-8")
    for name, content in (("original", source), ("updated", updated)):
        (package / f"{name}.py").write_text(content, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import runpy,sys; sys.path.insert(0,sys.argv[1]); "
                "runpy.run_module(sys.argv[2],run_name='__main__')",
                str(tmp_path),
                f"example.{name}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "custom"


@pytest.mark.parametrize(
    "source",
    [
        "import os.path; result = os.path.isabs('missing')\n",
        "import sys; import os.path\nresult = os.path.isabs('missing')\n",
        "def check():\n    import os.path\n    return os.path.isabs('missing')\nresult = check()\n",
        "def check():\n    import os.path\nresult = check()\n",
        "import os.path as osp; result = osp.isabs('missing'); del osp\n",
        "from os.path import isabs as present\nresult = present('missing')\ndel present\n",
        "from os import path as paths\nresult = paths.isabs('missing')\ndel paths\n",
        "import os.path\nresult = os.path.isabs('missing')\ndel os\n",
        "import os\ndef outer():\n    from os.path import isabs\n"
        "    def inner():\n        nonlocal isabs\n        isabs = None\n"
        "    return inner\nouter()()\nresult = os.path.basename('a/b')\n",
        "import os\ndef outer():\n    import os.path as osp\n"
        "    def inner():\n        nonlocal osp\n        osp = None\n"
        "    return inner\nouter()()\nresult = os.path.basename('a/b')\n",
    ],
)
def test_import_cleanup_leaves_valid_suites_and_semicolons(source: str) -> None:
    updated, count = fix_content(source)
    assert count > 0
    assert run(updated) == run(source)


@pytest.mark.parametrize("migrate_call", [False, True])
@pytest.mark.parametrize(
    ("header", "expected_doc", "keeps_statement"),
    [
        ("from os.path import basename\n'ordinary string'\n", None, True),
        (
            "from os.path import basename\nfrom os.path import dirname\n'ordinary string'\n",
            None,
            True,
        ),
        ("from os.path import basename; 'ordinary string'\n", None, True),
        (
            "'module documentation'\nfrom os.path import basename\n'ordinary string'\n",
            "module documentation",
            False,
        ),
        (
            "'module documentation'; from os.path import basename; 'ordinary string'\n",
            "module documentation",
            True,
        ),
        ("marker = True\nfrom os.path import basename\n'ordinary string'\n", None, False),
    ],
)
def test_import_cleanup_preserves_module_docstring(
    header: str, expected_doc: str | None, keeps_statement: bool, migrate_call: bool
) -> None:
    source = header + ("filename = basename('directory/file')\n" if migrate_call else "")
    source += "result = __doc__\n"
    updated, count = fix_content(source)
    assert "from os.path import basename" not in updated
    assert ("pass" in updated) is keeps_statement
    if migrate_call:
        assert count > 0
        assert "filename = Path(" in updated
    for content in (source, updated):
        module = ModuleType("fixture")
        exec(compile(content, "<fixture>", "exec"), module.__dict__)
        assert module.__doc__ == expected_doc
        assert module.result == expected_doc
        if migrate_call:
            assert module.filename == "file"
    assert fix_content(updated) == (updated, 0)


@pytest.mark.parametrize(
    "body, lookup",
    [
        ("from os.path import basename", "Paths.basename('a/b')"),
        ("if True:\n    from os.path import basename", "Paths.basename('a/b')"),
        ("class Nested:\n    from os.path import basename", "Paths.Nested.basename('a/b')"),
        (
            "from os.path import basename\nvalue = basename('a/b')",
            "Paths.basename('a/b') + Paths.value",
        ),
    ],
)
def test_import_cleanup_preserves_class_attributes(body: str, lookup: str) -> None:
    source = f"class Paths:\n{indent(body, '    ')}\nresult = {lookup}\n"
    updated, _ = fix_content(source)
    assert "from os.path import basename" in updated
    assert run(updated) == run(source)


@pytest.mark.parametrize("namespace", ["globals", "locals", "vars"])
@pytest.mark.parametrize("key", ["'osp'", "''.join(['o', 's', 'p'])"])
@pytest.mark.parametrize("rewrite_call", [False, True])
@pytest.mark.parametrize(
    ("imports", "accessor"),
    [
        ("", "{namespace}()"),
        ("import builtins\n", "builtins.{namespace}()"),
        ("import builtins as python_builtins\n", "python_builtins.{namespace}()"),
        ("from builtins import {namespace} as inspect_namespace\n", "inspect_namespace()"),
        ("inspect_namespace = {namespace}\n", "inspect_namespace()"),
        (
            "import builtins as python_builtins\ninspect_namespace = python_builtins.{namespace}\n",
            "inspect_namespace()",
        ),
        (
            "from builtins import {namespace} as original_namespace\n"
            "inspect_namespace = original_namespace\n",
            "inspect_namespace()",
        ),
        ("", "{namespace}(*[])"),
        ("", "{namespace}(**{})"),
        ("", "{namespace}(*[], **{})"),
        ("import builtins as python_builtins\n", "python_builtins.{namespace}(*())"),
        ("from builtins import {namespace} as inspect_namespace\n", "inspect_namespace(**{})"),
        ("import builtins\nnamespace_module = builtins\n", "namespace_module.{namespace}()"),
        ("import builtins\n", "getattr(builtins, '{namespace}')()"),
        ("import builtins\n", "builtins.__dict__['{namespace}']()"),
        ("", "__builtins__['{namespace}']()"),
        ("import builtins\n", "vars(builtins)['{namespace}']()"),
        (
            "from builtins import __dict__ as builtin_namespace\n",
            "builtin_namespace['{namespace}']()",
        ),
    ],
)
def test_import_cleanup_preserves_namespace_lookups(
    namespace: str, key: str, rewrite_call: bool, imports: str, accessor: str
) -> None:
    body = f"result = {accessor.replace('{namespace}', namespace)}[{key}].basename('a/b')\n"
    if rewrite_call:
        body += "filename = osp.basename('directory/file')\n"
    if namespace == "globals":
        source = "import os.path as osp\n" + body
    else:
        source = (
            "def check():\n"
            + indent("import os.path as osp\n" + body + "return result\n", "    ")
            + "result = check()\n"
        )
    source = imports.format(namespace=namespace) + source
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert "import os.path as osp" in updated
    assert run(updated) == run(source) == "b"
    assert fix_content(updated) == (updated, 0)


@pytest.mark.parametrize(
    ("namespace", "statement", "expected"),
    [
        ("eval", "result = {accessor}(\"osp.basename('a/b')\")", "b"),
        ("dir", "result = 'osp' in {accessor}()", True),
        ("exec", "{accessor}(\"result = osp.basename('a/b')\")", "b"),
    ],
)
@pytest.mark.parametrize("rewrite_call", [False, True])
@pytest.mark.parametrize(
    ("imports", "accessor"),
    [
        ("", "{namespace}"),
        ("import builtins as python_builtins\n", "python_builtins.{namespace}"),
        ("from builtins import {namespace} as inspect_namespace\n", "inspect_namespace"),
        ("inspect_namespace = {namespace}\n", "inspect_namespace"),
    ],
)
def test_import_cleanup_preserves_dynamic_namespace_access(
    namespace: str,
    statement: str,
    expected: object,
    rewrite_call: bool,
    imports: str,
    accessor: str,
) -> None:
    source = (
        "import os.path as osp\n"
        + imports.format(namespace=namespace)
        + statement.format(accessor=accessor.format(namespace=namespace))
        + "\n"
    )
    if rewrite_call:
        source += "filename = osp.basename('directory/file')\n"
    updated, count = fix_content(source)
    expected_changes = int(rewrite_call and namespace == "dir" and not imports.startswith("import"))
    assert count == expected_changes
    assert "import os.path as osp" in updated
    assert run(updated) == run(source) == expected
    assert fix_content(updated) == (updated, 0)


@pytest.mark.parametrize(
    ("imports", "accessor"),
    [
        ("", "vars"),
        ("import builtins\n", "builtins.vars"),
        ("import builtins as python_builtins\n", "python_builtins.vars"),
        ("from builtins import vars as inspect_namespace\n", "inspect_namespace"),
    ],
)
def test_import_cleanup_preserves_imports_with_object_vars(imports: str, accessor: str) -> None:
    source = (
        imports + "import os.path as osp\n"
        "from types import SimpleNamespace\n"
        "value = SimpleNamespace(filename=osp.basename('directory/file'))\n"
        f"result = {accessor}(value)['filename']\n"
    )
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert "import os.path as osp" in updated
    assert run(updated) == run(source) == "file"


@pytest.mark.parametrize(
    ("capability", "statement", "expression", "expected"),
    [
        ("exec", "{accessor}('osp = custom')", "osp.basename('a/b')", "custom"),
        (
            "eval",
            "{accessor}(\"globals().__setitem__('osp', custom)\")",
            "osp.basename('a/b')",
            "custom",
        ),
        ("globals", "{accessor}()['osp'] = custom", "osp.basename('a/b')", "custom"),
        ("locals", "{accessor}()['osp'] = custom", "osp.basename('a/b')", "custom"),
        ("vars", "{accessor}()['osp'] = custom", "osp.basename('a/b')", "custom"),
        ("exec", "{accessor}('Path = Custom')", "osp.basename('a/b')", "b"),
        ("exec", "{accessor}('str = repr')", "osp.join('a', 'b')", str(Path("a") / "b")),
    ],
)
@pytest.mark.parametrize(
    ("imports", "accessor"),
    [
        ("", "{capability}"),
        ("import builtins as python_builtins\n", "python_builtins.{capability}"),
        ("from builtins import {capability} as mutate_namespace\n", "mutate_namespace"),
        ("mutate_namespace = {capability}\n", "mutate_namespace"),
        ("", "__import__('builtins').{capability}"),
        ("import_builtins = __import__\n", "import_builtins('builtins').{capability}"),
        ("import importlib\n", "importlib.import_module('builtins').{capability}"),
        ("import sys\n", "sys.modules['builtins'].{capability}"),
        (
            "from builtins import __dict__ as builtin_namespace\n",
            "builtin_namespace['{capability}']",
        ),
    ],
)
def test_dynamic_namespace_writes_leave_source_unchanged(
    capability: str, statement: str, expression: str, expected: object, imports: str, accessor: str
) -> None:
    source = (
        "import os.path as osp\nfrom types import SimpleNamespace\n"
        "custom = SimpleNamespace(basename=lambda value: 'custom')\n"
        "Custom = lambda value: SimpleNamespace(name='custom')\n"
        + imports.format(capability=capability)
        + statement.format(accessor=accessor.format(capability=capability))
        + f"\nresult = {expression}\n"
    )
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source) == expected


@pytest.mark.parametrize("key", ["osp", "Path", "str"])
@pytest.mark.parametrize(
    "accessor",
    [
        "anchor.__globals__",
        "anchor.__getattribute__('__globals__')",
        "getattr(anchor, '__globals__')",
        "inspect_attribute(anchor, '__globals__')",
        pytest.param(
            "anchor.__builtins__['globals']()",
            marks=pytest.mark.skipif(
                sys.version_info < (3, 10), reason="Function builtins require 3.10"
            ),
        ),
        "sys._getframe().f_globals",
        "sys._getframe().f_locals",
        "sys.modules[anchor.__module__].__dict__",
    ],
)
def test_namespace_attribute_writes_leave_source_unchanged(key: str, accessor: str) -> None:
    source = (
        "import os.path as osp\nimport sys\nfrom types import SimpleNamespace\n"
        "def anchor(): pass\n"
        "custom = SimpleNamespace(basename=lambda value: 'custom')\n"
        "Custom = lambda value: SimpleNamespace(name='custom')\n"
        + ("inspect_attribute = getattr\n" if accessor.startswith("inspect_attribute") else "")
        + f"{accessor}[{key!r}] = "
        + {"osp": "custom", "Path": "Custom", "str": "repr"}[key]
        + "\nresult = "
        + ("osp.join('a', 'b')" if key == "str" else "osp.basename('a/b')")
        + "\n"
    )
    # Each fixture gets its own module dictionary, including a module entry for __dict__.
    module = ModuleType("pathlint_namespace_fixture")
    previous = sys.modules.get(module.__name__)
    sys.modules[module.__name__] = module
    try:
        updated, count = fix_content(source)
        assert (updated, count) == (source, 0)
        exec(compile(updated, "<namespace-write>", "exec"), module.__dict__)
        assert module.result == (
            "custom" if key == "osp" else str(Path("a") / "b") if key == "str" else "b"
        )
    finally:
        if previous is None:
            del sys.modules[module.__name__]
        else:
            sys.modules[module.__name__] = previous


def test_closure_cell_rebinding_leaves_source_unchanged() -> None:
    source = (
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(basename=lambda value: 'custom')\n"
        "def outer():\n    import os.path as osp\n"
        "    def check():\n        return osp.basename('a/b')\n    return check\n"
        "check = outer()\ncheck.__closure__[0].cell_contents = custom\nresult = check()\n"
    )
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source) == "custom"


@pytest.mark.parametrize(
    "declaration",
    [
        '__all__ = ["basename"]',
        '__all__ = list(("basename",))',
        "from export_names import __all__",
        "from export_names import exported as __all__\n__all__.append('basename')",
    ],
)
def test_import_cleanup_preserves_public_exports(tmp_path: Path, declaration: str) -> None:
    source = f"from os.path import basename\n{declaration}\nresult = basename('directory/file')\n"
    updated, count = fix_content(source)
    assert count > 0
    assert "from os.path import basename" in updated
    (tmp_path / "export_names.py").write_text(
        "__all__ = ['basename']\nexported = []\n", encoding="utf-8"
    )
    for content in (source, updated):
        (tmp_path / "fixture.py").write_text(content, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "from fixture import *; print(basename('directory/file'))",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "file\n"
        assert result.stderr == ""


@pytest.mark.parametrize("rewrite_call", [False, True])
@pytest.mark.parametrize(
    ("definition", "lookup", "allow_rewrites"),
    [
        ("def check(value: 'basename'): pass", "get_type_hints(check)['value'].__name__", True),
        ("def check() -> 'basename': pass", "get_type_hints(check)['return'].__name__", True),
        (
            "class Check:\n    value: 'basename'",
            "get_type_hints(Check, globalns=globals())['value'].__name__",
            False,
        ),
        (
            "def check(value: list['basename']): pass",
            "get_type_hints(check)['value'].__args__[0].__name__",
            True,
        ),
        (
            "Alias = 'basename'\ndef check(value: Alias): pass",
            "get_type_hints(check)['value'].__name__",
            True,
        ),
        (
            "def check(value): pass\ncheck.__annotations__ = {'value': 'basename'}",
            "get_type_hints(check)['value'].__name__",
            True,
        ),
        (
            "import types\n__annotations__ = {'value': 'basename'}\n"
            "module = types.ModuleType('fixture')\nmodule.__dict__.update(globals())",
            "get_type_hints(module)['value'].__name__",
            False,
        ),
    ],
)
def test_import_cleanup_preserves_quoted_annotations(
    definition: str, lookup: str, allow_rewrites: bool, rewrite_call: bool
) -> None:
    source = (
        "from os.path import basename\nfrom typing import get_type_hints\n"
        + definition
        + f"\nresult = {lookup}\n"
        + ("filename = basename('directory/file')\n" if rewrite_call else "")
    )
    updated, count = fix_content(source)
    assert count == int(rewrite_call and allow_rewrites)
    assert "from os.path import basename" in updated
    assert run(updated) == run(source) == "basename"


def test_crlf_and_final_newline_are_preserved() -> None:
    original = "import os.path\r\nresult = os.path.isabs('missing')\r\n"
    updated, count = fix_content(original)
    assert count > 0
    assert updated.endswith("\r\n")
    assert "\n" not in updated.replace("\r\n", "")
    assert run(updated) == run(original)


def test_invalid_source_is_not_transformed() -> None:
    with pytest.raises(SyntaxError):
        fix_content("import os\nos.path.isabs(\n")


def test_result_for_missing_path_is_false() -> None:
    # Exercise both versions against the host without relying on mocked pathlib methods.
    original = "import os\nresult = os.path.exists('definitely-not-a-pathlint-test-file')\n"
    updated, count = fix_content(original)
    assert (updated, count) == (original, 0)
    assert run(original) is run(updated) is False


@pytest.mark.parametrize(
    "source",
    [
        "import os; result = os.path.isabs('missing')\n",
        '"""Docstring."""; import os; result = os.path.isabs("missing")\n',
        "from __future__ import annotations; import os; result = os.path.isabs('missing')\n",
        "import os\n"
        "def check():\n    return os.path.isabs('missing')\n"
        "result = check()\n"
        "from pathlib import Path\n",
        "import os\n"
        "def check():\n    return os.path.isabs('missing')\n"
        "result = check()\n"
        "import pathlib\n",
        "import os\n"
        "try:\n    raise ValueError()\n"
        "except Exception as Path:\n    result = os.path.isabs('missing')\n",
        "from pathlib import Path\nimport os\ntry:\n    raise ValueError()\n"
        "except ValueError as Path:\n    from pathlib import Path\n"
        "result = os.path.basename('a/b')\n",
        "from pathlib import Path as P\nimport os\ntry:\n    raise ValueError()\n"
        "except ValueError as P:\n    from pathlib import Path as P\n"
        "result = os.path.basename('a/b')\n",
        "import pathlib\nimport os\ntry:\n    raise ValueError()\n"
        "except ValueError as pathlib:\n    import pathlib\n"
        "result = os.path.basename('a/b')\n",
        "from pathlib import Path as __P\nimport os\n"
        "class Example:\n    result = os.path.basename('a/b')\n"
        "result = Example.result\n",
        "import pathlib as __paths\nimport os\n"
        "class Example:\n    result = os.path.basename('a/b')\n"
        "result = Example.result\n",
        "from pathlib import Path as __P\nimport os\n"
        "class Example:\n    def check(self):\n        return os.path.basename('a/b')\n"
        "result = Example().check()\n",
        pytest.param(
            "from pathlib import Path\nimport os\ntry:\n"
            "    raise ExceptionGroup('failure', [ValueError()])\n"
            "except* ValueError as Path:\n    from pathlib import Path\n"
            "result = os.path.basename('a/b')\n",
            marks=pytest.mark.skipif(
                sys.version_info < (3, 11), reason="Requires exception groups"
            ),
        ),
    ],
)
def test_generated_import_is_available_without_shadowing(source: str) -> None:
    updated, count = fix_content(source)
    assert count > 0
    assert run(updated) == run(source)
    assert fix_content(updated) == (updated, 0)


@pytest.mark.parametrize("condition", ["True", "False"])
def test_ambiguous_branch_binding_is_not_rewritten(condition: str) -> None:
    source = (
        "from types import SimpleNamespace\n"
        "if " + condition + ":\n"
        "    os = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        "else:\n"
        "    import os\n"
        "result = os.path.isabs('missing')\n"
    )
    updated, count = fix_content(source)
    assert count == 0
    assert updated == source
    assert run(updated) == run(source)


@pytest.mark.parametrize("expression", ["os.path.basename('a/.')", "os.path.join('a/.', 'b')"])
def test_dot_components_are_not_normalized(expression: str) -> None:
    source = "import os\nresult = " + expression + "\n"
    assert fix_content(source) == (source, 0)


@pytest.mark.parametrize(
    "expression",
    ["os.path.join('a', 'b')", "os.path.dirname('a/b')", "os.path.expanduser('a')"],
)
def test_shadowed_str_does_not_change_result(expression: str) -> None:
    source = "import os\nstr = lambda value: 'custom'\nresult = " + expression + "\n"
    assert fix_content(source) == (source, 0)


@pytest.mark.skipif(not hasattr(ast, "Match"), reason="Pattern matching requires Python 3.10")
def test_match_capture_does_not_shadow_generated_constructor() -> None:
    source = (
        "import os\n"
        "match {'value': 42}:\n"
        "    case {'value': Path}:\n"
        "        result = os.path.isabs('missing')\n"
    )
    updated, count = fix_content(source)
    assert count > 0
    assert run(updated) == run(source)


@pytest.mark.skipif(not hasattr(ast, "Match"), reason="Pattern matching requires Python 3.10")
def test_match_capture_is_not_mistaken_for_os() -> None:
    source = (
        "import os\nfrom types import SimpleNamespace\n"
        "def check(value):\n"
        "    match value:\n"
        "        case {'os': os}:\n"
        "            return os.path.isabs('missing')\n"
        "result = check({'os': SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))})\n"
    )
    assert fix_content(source) == (source, 0)
    assert run(source) == "custom"


@pytest.mark.skipif(not hasattr(ast, "Match"), reason="Pattern matching requires Python 3.10")
@pytest.mark.parametrize(
    ("match", "expected", "changes"),
    [
        (
            "match custom:\n    case os if False:\n        pass\n"
            "    case _:\n        result = os.path.isabs('missing')\n",
            "custom",
            False,
        ),
        (
            "match custom:\n    case os if False:\n        import os\n"
            "result = os.path.isabs('missing')\n",
            "custom",
            False,
        ),
        (
            "match 1:\n    case _ if (os := custom) and False:\n        pass\n"
            "    case _:\n        result = os.path.isabs('missing')\n",
            "custom",
            False,
        ),
        (
            "match 1:\n    case 0:\n        os = custom\n"
            "    case _:\n        result = os.path.isabs('missing')\n",
            False,
            True,
        ),
    ],
)
def test_failed_match_guards_retain_bindings(match: str, expected: object, changes: bool) -> None:
    source = (
        "import os\nfrom types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n" + match
    )
    updated, count = fix_content(source)
    assert bool(count) is changes
    assert run(updated) == run(source) == expected


@pytest.mark.parametrize(
    "block",
    [
        "try:\n    os = custom\nexcept Exception:\n    import os\n",
        "os = custom\nfor item in []:\n    import os\n",
        "os = custom\nwhile False:\n    import os\n",
        "os = custom\ntry:\n    pass\nexcept Exception:\n    import os\nfinally:\n    result = os.path.isabs('missing')\n",
        "import os\ntry:\n    os = custom\n    raise ValueError()\n    import os\n"
        "except ValueError:\n    result = os.path.isabs('missing')\n",
        "import os\ntry:\n    try:\n        raise ValueError()\n"
        "    except ValueError:\n        os = custom\n        raise TypeError()\n        import os\n"
        "    finally:\n        result = os.path.isabs('missing')\n"
        "except TypeError:\n    pass\n",
        "import os\ntry:\n    try:\n        pass\n"
        "    except ValueError:\n        pass\n"
        "    else:\n        os = custom\n        raise TypeError()\n        import os\n"
        "    finally:\n        result = os.path.isabs('missing')\n"
        "except TypeError:\n    pass\n",
        "from contextlib import suppress\nos = custom\nwith suppress(ValueError):\n"
        "    int('bad')\n    import os\n",
        "from contextlib import suppress\nimport os\nwith suppress(ValueError):\n"
        "    os = custom\n    int('bad')\n    import os\n",
        "from contextlib import suppress\nos = custom\n"
        "with suppress(ValueError), int('bad') as os:\n    import os\n",
        "import os\n[(os := custom) for item in [0]]\n",
        "import os\n{(os := custom).path.isabs('missing') for item in [0]}\n",
        "import os\n{item: (os := custom) for item in [0]}\n",
        "import os\n[[(os := custom) for item in [0]] for outer in [0]]\n",
        "import os\nsequence = ((os := custom) for item in [0])\nnext(sequence)\n",
        "def check():\n    import os\n    [(os := custom) for item in [0]]\n    return os\nos = check()\n",
        "import os\n[(os.path.isabs('missing'), (os := custom)) for item in [0, 1]]\n",
    ],
)
def test_conditional_imports_do_not_override_custom_objects(block: str) -> None:
    source = (
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        + block
        + "result = os.path.isabs('missing')\n"
    )
    updated, count = fix_content(source)
    assert count == 0
    assert updated == source
    assert run(updated) == run(source) == "custom"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_suppressed_context_import_preserves_argument_binding(asynchronous: bool) -> None:
    keyword = "async " if asynchronous else ""
    decorator = "asynccontextmanager" if asynchronous else "contextmanager"
    source = (
        f"from contextlib import {decorator}\n"
        "from types import SimpleNamespace\nimport asyncio\n"
        f"@{decorator}\n{keyword}def suppressor():\n"
        "    try:\n        yield\n    except ValueError:\n        pass\n"
        f"{keyword}def check(p):\n"
        f"    {keyword}with suppressor():\n"
        "        int('bad')\n        import os.path as p\n"
        "    return p.basename('a/b')\n"
        "custom = SimpleNamespace(basename=lambda value: 'custom')\n"
        + ("result = asyncio.run(check(custom))\n" if asynchronous else "result = check(custom)\n")
    )
    assert fix_content(source) == (source, 0)
    assert run(source) == "custom"


@pytest.mark.parametrize(
    "loop",
    ["for item in values", "while values and values.pop()", "async for item in iterate(values)"],
)
@pytest.mark.parametrize(
    ("values", "body", "alternative", "following", "expected", "changes"),
    [
        (
            "[]",
            "import os",
            "result = os.path.isabs('missing')",
            "return result",
            "custom",
            False,
        ),
        (
            "[]",
            "pass",
            "import os\n        result = os.path.isabs('missing')",
            "return result",
            False,
            False,
        ),
        (
            "[True]",
            "break",
            "import os",
            "return os.path.isabs('missing')",
            "custom",
            False,
        ),
        (
            "[True]",
            "break\n        import os",
            "import os",
            "return os.path.isabs('missing')",
            "custom",
            False,
        ),
    ],
)
def test_loop_else_bindings_preserve_results(
    loop: str,
    values: str,
    body: str,
    alternative: str,
    following: str,
    expected: object,
    changes: bool,
) -> None:
    source = (
        "import asyncio\n"
        "from types import SimpleNamespace\n"
        "async def iterate(values):\n"
        "    for value in values:\n"
        "        yield value\n"
        "async def check():\n"
        f"    values = {values}\n"
        "    os = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        f"    {loop}:\n"
        f"        {body}\n"
        "    else:\n"
        f"        {alternative}\n"
        f"    {following}\n"
        "result = asyncio.run(check())\n"
    )
    updated, count = fix_content(source)
    assert bool(count) is changes
    assert run(updated) == run(source) == expected


@pytest.mark.parametrize(
    "loop",
    ["for item in (os := custom, [])[1]", "while (os := custom) and False"],
)
def test_loop_header_bindings_apply_before_else(loop: str) -> None:
    source = (
        "import os\n"
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        f"{loop}:\n"
        "    pass\n"
        "else:\n"
        "    result = os.path.isabs('missing')\n"
    )
    updated, count = fix_content(source)
    assert count == 0
    assert updated == source
    assert run(updated) == run(source) == "custom"


@pytest.mark.parametrize(
    "loop",
    ["for item in values", "while values and values.pop()", "async for item in iterate(values)"],
)
@pytest.mark.parametrize(
    ("preamble", "body", "alternative", "following", "expected", "changes"),
    [
        (
            "import os as alias\nresults = []",
            "results.append(alias.path.isabs('missing'))\nalias = custom",
            "pass",
            "return results",
            [False, "custom"],
            False,
        ),
        (
            "import os\nfrom pathlib import Path as constructor\nresults = []",
            "results.append(os.path.isabs('missing'))\n"
            "constructor = lambda value: SimpleNamespace(is_absolute=lambda: 'custom')",
            "pass",
            "return results",
            [False, False],
            True,
        ),
        (
            "import os",
            "os = custom\nbreak\nimport os",
            "import os",
            "return os.path.isabs('missing')",
            "custom",
            False,
        ),
    ],
)
def test_loop_rebindings_preserve_results(
    loop: str,
    preamble: str,
    body: str,
    alternative: str,
    following: str,
    expected: object,
    changes: bool,
) -> None:
    source = (
        "import asyncio\n"
        "from types import SimpleNamespace\n"
        "async def iterate(values):\n"
        "    for value in values:\n"
        "        yield value\n"
        "async def check():\n"
        "    values = [True, True]\n"
        "    custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        + indent(preamble + "\n", "    ")
        + f"    {loop}:\n"
        + indent(body + "\n", "        ")
        + "    else:\n"
        + indent(alternative + "\n", "        ")
        + indent(following + "\n", "    ")
        + "result = asyncio.run(check())\n"
    )
    updated, count = fix_content(source)
    assert bool(count) is changes
    assert run(updated) == run(source) == expected


@pytest.mark.parametrize(
    ("binding_expression", "binding_statement"),
    [("", "    alias = custom\n"), (" and (alias := custom)", "")],
)
def test_while_test_rebinding_preserves_results(
    binding_expression: str, binding_statement: str
) -> None:
    source = (
        "import os as alias\n"
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        "values = [True, True]\n"
        "result = 0\n"
        f"while alias.path.isabs('missing') is False{binding_expression} "
        "and values and values.pop():\n"
        "    result += 1\n" + binding_statement
    )
    updated, count = fix_content(source)
    assert count == 0
    assert updated == source
    assert run(updated) == run(source) == 1


@pytest.mark.parametrize(
    "definition",
    [
        "def bind(value=(alias := custom)):\n    return value",
        "@((alias := custom), lambda function: function)[1]\ndef bind():\n    pass",
        "class Bind(((alias := custom), object)[1]):\n    pass",
    ],
)
def test_definition_expressions_rebind_enclosing_loop_names(definition: str) -> None:
    source = (
        "import os as alias\n"
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        "result = []\n"
        "for value in [1, 2]:\n"
        "    result.append(alias.path.isabs('missing'))\n" + indent(definition + "\n", "    ")
    )
    updated, count = fix_content(source)
    assert count == 0
    assert updated == source
    assert run(updated) == run(source) == [False, "custom"]


@pytest.mark.parametrize(
    ("body", "expected", "changes"),
    [
        (
            "osp.basename = lambda value: 'custom'\nresult = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "osp.basename: object = lambda value: 'custom'\nresult = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "class Replacement:\n"
            "    def __radd__(self, previous):\n        return lambda value: 'custom'\n"
            "osp.basename += Replacement()\nresult = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "import os.path as other\nosp.basename = lambda value: 'custom'\n"
            "result = other.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "osp.basename = lambda value: 'custom'\nimport os.path as osp\n"
            "result = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "osp.basename = lambda value: 'custom'\nfrom os.path import basename as present\n"
            "result = present('directory/file')\n",
            "custom",
            False,
        ),
        (
            "del osp.basename\ntry:\n    result = osp.basename('directory/file')\n"
            "except AttributeError:\n    result = 'missing'\n",
            "missing",
            False,
        ),
        (
            "def reader():\n    return osp.basename('directory/file')\n"
            "def writer():\n    osp.basename = lambda value: 'custom'\n"
            "writer()\nresult = reader()\n",
            "custom",
            False,
        ),
        (
            "import os\nos.path = SimpleNamespace(basename=lambda value: 'custom')\n"
            "result = os.path.basename('directory/file')\n",
            "custom",
            True,  # The unused osp import is removed; the customized call remains.
        ),
        (
            "osp.basename = lambda value: 'custom'\nresult = osp.dirname('directory/file')\n",
            "directory",
            True,
        ),
        (
            "if False:\n    osp = object()\n"
            "osp.basename = lambda value: 'custom'\nimport os.path as other\n"
            "result = other.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "if False:\n    import math as osp\n"
            "osp.basename = lambda value: 'custom'\nimport os.path as other\n"
            "result = other.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "import os as system\nif False:\n    system = object()\n"
            "system.path.basename = lambda value: 'custom'\n"
            "result = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "if False:\n    osp = object()\ndel osp.basename\nimport os.path as other\n"
            "try:\n    result = other.basename('directory/file')\n"
            "except AttributeError:\n    result = 'missing'\n",
            "missing",
            False,
        ),
    ],
)
def test_mutated_path_attributes_preserve_results(body: str, expected: str, changes: bool) -> None:
    source = "import os.path as osp\nfrom types import SimpleNamespace\n" + body
    updated, count = fix_content(source)
    assert bool(count) is changes
    # Isolate module mutations and restore before subprocess coverage shuts down.
    program = (
        "import os, sys\n"
        "original_path = os.path\noriginal_basename = original_path.basename\n"
        "namespace = {}\ntry:\n"
        "    exec(compile(sys.argv[1], '<fixture>', 'exec'), namespace)\n"
        "    print(namespace['result'])\nfinally:\n"
        "    os.path = original_path\n    original_path.basename = original_basename\n"
    )
    for content in (source, updated):
        result = subprocess.run(
            [sys.executable, "-c", program, content],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected + "\n"
        assert result.stderr == ""


@pytest.mark.parametrize(
    "mutation",
    [
        "paths.Path = lambda value: None",
        "del paths.Path",
        "paths.Path = lambda value: None\nfrom pathlib import Path",
        "from pathlib import Path as constructor\nconstructor.exists = lambda self: True",
        "def writer():\n    paths.Path = lambda value: None\nwriter()",
        "paths.PurePath.name = property(lambda self: 'custom')",
        "from pathlib import PurePath as constructor\nconstructor.name = property(lambda self: 'custom')",
        "paths.PosixPath.name = property(lambda self: 'custom')",
    ],
)
def test_mutated_pathlib_constructor_is_not_used(mutation: str) -> None:
    source = (
        "import os\nimport pathlib as paths\n" + mutation + "\n"
        "result = os.path.basename('directory/file')\n"
    )
    updated, count = fix_content(source)
    assert count == 0
    assert updated == source
    program = (
        "import pathlib, sys\n"
        "original_path = pathlib.Path\noriginal_exists = original_path.exists\n"
        "original_name = pathlib.PurePath.name\n"
        "original_posix_name = pathlib.PosixPath.__dict__.get('name')\n"
        "namespace = {}\ntry:\n"
        "    exec(compile(sys.argv[1], '<fixture>', 'exec'), namespace)\n"
        "    print(namespace['result'])\nfinally:\n"
        "    pathlib.Path = original_path\n    original_path.exists = original_exists\n"
        "    pathlib.PurePath.name = original_name\n"
        "    if original_posix_name is None:\n"
        "        if 'name' in pathlib.PosixPath.__dict__:\n            del pathlib.PosixPath.name\n"
        "    else:\n        pathlib.PosixPath.name = original_posix_name\n"
    )
    for content in (source, updated):
        result = subprocess.run(
            [sys.executable, "-c", program, content],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "file\n"
        assert result.stderr == ""
