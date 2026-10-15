"""Behavioral regressions for AST-backed, source-preserving transformations."""

import ast
import ntpath
import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from textwrap import indent
from types import ModuleType
from typing import Optional, Union

import pytest

from pathlint.autofix import fix_content
from pathlint.linter import OSPathDetector, lint_file
from tests._forms import ALIAS_COPY_FORMS
from tests.helpers import run


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
        ("import os", "os.path.join('directory', 'file',)"),
        ("import os", "os.path.join('root', 'subdir', 'file.txt')"),
        ("import os", "os.path.isabs('файл')"),
        (
            "import os",
            "os.path.join(\n    'root',  # ) should not end the call\n"
            "    'subdir',\n    'file.txt',\n)",
        ),
        ("import pathlib\nimport os", "os.path.isabs(FILE)"),
        ("import pathlib as paths\nimport os", "os.path.isabs(FILE)"),
        ("from pathlib import Path as P\nimport os", "os.path.isabs(FILE)"),
        ("import os.path as paths", "(paths.basename)('directory/file')"),
        ("import os", "((os.path.isabs))(FILE)"),
        ("from os.path import isabs as present", "(present)(FILE)"),
        ("import os", "(\n    (os.path.basename)  # callable (comment\n    ('directory/file')\n)"),
    ],
)
def test_supported_calls_preserve_results(imports: str, expression: str) -> None:
    original = imports + "\nresult = " + expression.replace("FILE", "'directory/file'") + "\n"
    updated, count = fix_content(original)
    assert count > 0
    assert run(updated) == run(original)
    detector = OSPathDetector()
    detector.visit(ast.parse(updated))
    import_lines = {
        node.lineno
        for node in ast.walk(ast.parse(updated))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    }
    assert set(detector.offenses) <= import_lines
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
def test_windows_separator_spellings_stay_unchanged(method: str, args: tuple[str, ...]) -> None:
    """Keep literal spellings whose Windows string results pathlib normalizes."""
    original_result = getattr(ntpath, method)(*args)
    path = PureWindowsPath(*args)
    replacement_result = (
        path.name if method == "basename" else str(path.parent if method == "dirname" else path)
    )
    assert original_result != replacement_result
    original = (
        f"import os.path as osp\nresult = osp.{method}({', '.join(repr(arg) for arg in args)})\n"
    )
    assert fix_content(original, platform="win32") == (original, 0)
    if sys.platform != "win32":
        updated, count = fix_content(original)
        assert count > 0
        assert run(updated) == run(original)


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
    method: str, args: tuple[str, ...]
) -> None:
    """Retain supported Windows literals when their string results are exact."""
    expected = getattr(ntpath, method)(*args)
    path = PureWindowsPath(*args)
    replacement_result = (
        path.name if method == "basename" else str(path.parent if method == "dirname" else path)
    )
    assert expected == replacement_result
    original = (
        f"import os.path as osp\nresult = osp.{method}({', '.join(repr(arg) for arg in args)})\n"
    )
    updated, count = fix_content(original, platform="win32")
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
    detector = OSPathDetector()
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
    assert count == 1
    assert "from os.path import exists, isfile, isdir, basename\n" in updated
    assert "_Path('directory/file').name" in updated
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
    tmp_path: Path, source: str, expected: str, source_env: dict[str, str]
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
    for content in (source, updated):
        result = subprocess.run(
            [sys.executable, "-c", content + "\nprint(result)\n"],
            cwd=tmp_path,
            env=source_env,
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
    detector = OSPathDetector()
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
        "os.path.expanduser('~')",
        "os.path.expanduser('~/file')",
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


@pytest.mark.parametrize("home", [".", "/tmp/directory//subdirectory", ""])
@pytest.mark.parametrize("value", ["~", "~/file"])
def test_home_expansion_preserves_environment_spellings(
    monkeypatch: pytest.MonkeyPatch, home: str, value: str
) -> None:
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    source = f"import os.path as osp\nresult = osp.expanduser({value!r})\n"
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source)


@pytest.mark.parametrize("method", ["isabs", "basename", "dirname", "expanduser", "join"])
@pytest.mark.parametrize(
    "argument",
    [
        "b'directory/' + b'file'",
        "b'directory/file' * 1",
        "1 * b'directory/file'",
        "b'directory/%s' % b'file'",
        "b'directory/file'[:]",
        "b'directory/file'[slice(None)]",
        "(b'directory/' + b'file')[1:]",
        "[b'directory/file'][0]",
        "(b'directory/file',)[0]",
        "{'path': b'directory/file'}['path']",
        "[b'directory/file'][0:1][0]",
        "(b'directory/file',)[0:1][0]",
        "[[b'directory/file']][0][0]",
        "[*[b'directory/file']][0]",
        "{**{'path': b'directory/file'}}['path']",
        "b'directory/file'.split(b':')[0]",
        "b'directory/file'.partition(b':')[0]",
        "os.path.splitext(b'directory/file')[0]",
        "os.path.split(b'directory/file')[1]",
        "os.fsencode('directory/file')",
        "bytes.upper(b'directory/file')",
        "next(iter([b'directory/file']))",
        "[part for part in [b'directory/file']][0]",
        "[b'directory/file' for _ in range(1)][0]",
        "next(iter({b'directory/file': None}))",
        "next(iter({b'directory/file': None for _ in range(1)}))",
        "next(iter({b'directory/file': None}.keys()))",
        "list({b'directory/file': None}.items())[0][0]",
        "b'directory/file' if flag else b'other/file'",
        "'directory/file' if not flag else b'directory/file'",
        "b'' or b'directory/file'",
        "flag and b'directory/file'",
        "(value := b'directory/file')",
        "bytes(b'directory/file')",
        "bytes('directory/file', 'utf-8')",
        "'directory/file'.encode()",
        "('directory/' + 'file').encode('utf-8')",
        "f'directory/{name}'.encode()",
        "bytes.fromhex('612f62')",
        "b'directory/file'.upper()",
        "b'directory/other'.replace(b'other', b'file')",
        "b' directory/file '.strip()",
        "b'/'.join([b'directory', b'file'])",
        "os.path.join(b'directory', b'file')",
        "os.path.normpath(path=b'directory/file')",
        "os.path.join(a=b'directory/file')",
        "os.path.relpath(path=b'directory/file', start=b'directory')",
        "os.path.join(*[b'directory', b'file'])",
        "os.path.normpath(**{'path': b'directory/file'})",
    ],
)
def test_byte_path_expressions_preserve_results(method: str, argument: str) -> None:
    source = f"import os\nflag = True\nname = 'file'\nresult = os.path.{method}({argument})\n"
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "import os\ndirectory = b'directory'\nresult = os.path.join(directory, b'fi' + b'le')\n",
            b"directory/file",
            id="test_join_with_byte_expression_in_later_argument_stays_unchanged",
        ),
        pytest.param(
            "import os\nresult = os.path.basename({**{b'path': 'directory/file'}}[b'path'])\n",
            "file",
            id="test_byte_keys_in_unpacked_mapping_paths_are_conservatively_skipped",
        ),
        pytest.param(
            "import os\nclass TextPath(str):\n    def __getitem__(self, index):\n"
            "        return b'directory/file'\n"
            "class Source:\n    def __str__(self):\n        return TextPath('directory/file')\n"
            "result = os.path.basename(str(Source())[0])\n",
            b"file",
            id="test_text_subclass_index_byte_results_stay_unchanged",
        ),
        pytest.param(
            "import os.path as osp\n"
            "def check():\n    return osp.basename('directory\\\\file')\n"
            "import ntpath as osp\n"
            "result = check()\n"
            "import os.path as osp\n",
            "file",
            id="test_deferred_alias_with_conflicting_import_origins_is_not_rewritten",
        ),
        # Preserve nested deferred calls when their source contributes to a debug label.
        pytest.param(
            "import os.path as p\n"
            """label = f"{(lambda: p.basename('a/b'))=}"\n"""
            "result = label.split('=', 1)[0]\n",
            "(lambda: p.basename('a/b'))",
            id="test_debug_fstring_deferred_lambda_retains_its_label",
        ),
        # Analyze assignments inside preserved debug fields before later calls.
        pytest.param(
            "import os\nfrom types import SimpleNamespace\n"
            "custom = SimpleNamespace(path=SimpleNamespace(basename=str.upper))\n"
            """label = f"{(os := custom)=}"\n"""
            "result = os.path.basename('a/b')\n",
            "A/B",
            id="test_debug_fstring_walrus_still_updates_bindings",
        ),
        pytest.param(
            "import os.path as osp\n"
            "def configure():\n    global osp\n    del osp\nconfigure()\n"
            "try:\n    result = osp.basename('a/b')\n"
            "except NameError:\n    result = 'missing'\n",
            "missing",
            id="test_explicit_global_deletion_preserves_immediate_name_error",
        ),
        pytest.param(
            "from types import SimpleNamespace\n"
            "custom = SimpleNamespace(basename=lambda value: 'custom')\n"
            "def outer():\n    import os.path as osp\n"
            "    def check():\n        return osp.basename('a/b')\n    return check\n"
            "check = outer()\ncheck.__closure__[0].cell_contents = custom\nresult = check()\n",
            "custom",
            id="test_closure_cell_rebinding_leaves_source_unchanged",
        ),
    ],
)
def test_unrewritten_sources_preserve_runtime_results(source: str, expected: object) -> None:
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source) == expected


@pytest.mark.parametrize(
    ("imports", "call"),
    [
        ("import os.path as paths", "paths.join(b'directory', b'file')"),
        ("from os.path import join as connect", "connect(b'directory', b'file')"),
        ("import os.path as paths", "paths.join(a=b'directory/file')"),
        ("from os.path import normpath as normalize", "normalize(path=b'directory/file')"),
        (
            "from os.path import join",
            "join(b'directory', b'file') if True else (join := None)",
        ),
        ("from os.path import join", "join(b'directory', b'file') or (join := None)"),
        ("from os.path import join", "join((join := b'directory'), b'file')"),
        ("import os.path as paths", "paths.splitext(b'directory/file')[0]"),
        ("from os.path import split as divide", "divide(b'directory/file')[1]"),
        ("from os import fsencode as encode_path", "encode_path('directory/file')"),
        ("from builtins import bytes as make_bytes", "make_bytes([97, 47, 98])"),
        ("from builtins import bytes as byte_type", "byte_type.upper(b'directory/file')"),
        ("import os.path as paths", "paths.join(*[b'directory', b'file'])"),
        ("import os.path as paths", "paths.normpath(**{'path': b'directory/file'})"),
        ("import codecs", "codecs.decode(b'66696c65', 'hex')"),
        ("import codecs as transforms", "transforms.decode(b'ZmlsZQ==', 'base64')"),
        ("from codecs import decode as convert", "convert('66696c65', 'hex')"),
        ("import base64", "base64.b64decode('ZGlyZWN0b3J5L2ZpbGU=')"),
        ("import binascii", "binascii.unhexlify('6469726563746f72792f66696c65')"),
        ("from base64 import b64decode as convert", "convert('ZGlyZWN0b3J5L2ZpbGU=')"),
    ],
)
def test_nested_aliased_byte_path_calls_stay_unchanged(imports: str, call: str) -> None:
    source = f"import os\n{imports}\nresult = os.path.basename({call})\n"
    assert fix_content(source) == (source, 0)
    assert isinstance(run(source), bytes)


@pytest.mark.parametrize(
    "argument",
    [
        "'directory/' + 'file'",
        "'directory/file' * 1",
        "1 * 'directory/file'",
        "'directory/%s' % 'file'",
        "'directory/file'[:]",
        "['directory/file'][0]",
        "('directory/file',)[0]",
        "{'path': 'directory/file'}['path']",
        "['directory/file'][0:1][0]",
        "('directory/file',)[0:1][0]",
        "[['directory/file']][0][0]",
        "'' or 'directory/file'",
        "(value := 'directory/file')",
        "f'directory/{\"file\"}'",
    ],
)
def test_nonliteral_text_path_expressions_stay_unchanged(argument: str) -> None:
    source = f"import os\nresult = os.path.basename({argument})\n"
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    assert run(updated) == run(source)


@pytest.mark.parametrize(
    ("method", "argument", "value", "expected"),
    [
        ("dirname", "value", "'file'", ""),
        ("basename", "value", "'directory/'", ""),
        ("join", "'directory', value", "''", "directory" + os.sep),
        (
            "expanduser",
            "value",
            "'~__pathlint_nonexistent_user__/file'",
            "~__pathlint_nonexistent_user__/file",
        ),
        ("isabs", "value", "b'file'", False),
    ],
)
def test_unknown_path_values_preserve_results(
    method: str, argument: str, value: str, expected: object
) -> None:
    source = f"import os.path as osp\nvalue = {value}\nresult = osp.{method}({argument})\n"
    assert fix_content(source) == (source, 0)
    assert run(source) == expected


@pytest.mark.parametrize("builtin", ["str", "repr", "ascii"])
def test_imported_text_converters_are_conservatively_skipped(builtin: str) -> None:
    source = (
        f"import os\nfrom builtins import {builtin} as render\n"
        "result = os.path.basename(render(b'directory/file'))\n"
    )
    assert fix_content(source) == (source, 0)
    assert isinstance(run(source), str)


@pytest.mark.parametrize("name", ["str", "repr", "ascii", "chr", "custom_wrapper"])
@pytest.mark.parametrize("binding", ["module", "global", "generator"])
def test_custom_byte_wrappers_stay_unchanged(name: str, binding: str) -> None:
    call = f"os.path.basename({name}(b'directory/file'))"
    if binding == "global":
        body = (
            f"def rebind():\n    global {name}\n    {name} = lambda value: value\n"
            f"rebind()\nresult = {call}\n"
        )
    elif binding == "generator":
        body = (
            f"deferred = ({call} for _ in [0])\n{name} = lambda value: value\n"
            "result = next(deferred)\n"
        )
    else:
        body = f"{name} = lambda value: value\nresult = {call}\n"
    source = "import os\n" + body
    assert fix_content(source) == (source, 0)
    assert run(source) == b"file"


@pytest.mark.parametrize(
    ("method", "operator"),
    [
        ("__eq__", "=="),
        ("__ne__", "!="),
        ("__lt__", "<"),
        ("__le__", "<="),
        ("__gt__", ">"),
        ("__ge__", ">="),
    ],
)
def test_rich_comparison_byte_results_stay_unchanged(method: str, operator: str) -> None:
    source = (
        "import os\n"
        f"class Comparison:\n    def {method}(self, other):\n        return b'directory/file'\n"
        f"result = os.path.basename(Comparison() {operator} 0)\n"
    )
    assert fix_content(source) == (source, 0)
    assert run(source) == b"file"


@pytest.mark.parametrize("value", [b"directory/file", "directory/file"])
def test_unknown_call_return_types_stay_unchanged(value: Union[bytes, str]) -> None:
    source = (
        f"import os\ndef get_path():\n    return {value!r}\nresult = os.path.basename(get_path())\n"
    )
    assert fix_content(source) == (source, 0)
    assert run(source) == (b"file" if isinstance(value, bytes) else "file")


@pytest.mark.parametrize("method", ["decode", "hex"])
@pytest.mark.parametrize("rebind_fsencode", [False, True])
def test_unknown_receiver_text_method_names_stay_unchanged(
    method: str, rebind_fsencode: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        f"import os\nclass Decoder:\n    def {method}(self):\n        return b'directory/file'\n"
        "def get_decoder():\n    return Decoder()\n"
        + ("os.fsencode = lambda value: Decoder()\n" if rebind_fsencode else "")
        + "result = os.path.basename("
        + ("os.fsencode('x')" if rebind_fsencode else "get_decoder()")
        + f".{method}())\n"
    )
    assert fix_content(source) == (source, 0)
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsencode", os.fsencode)
        assert run(source) == b"file"


@pytest.mark.parametrize("method", ["decode", "hex"])
@pytest.mark.parametrize("factory", ["bytes(Source())", "'x'.encode('pathlint-review-codec')"])
def test_byte_subclass_text_methods_stay_unchanged(method: str, factory: str) -> None:
    source = (
        "import os, codecs\n"
        f"class BytePath(bytes):\n    def {method}(self):\n        return b'directory/file'\n"
        "class Source:\n    def __bytes__(self):\n        return BytePath(b'x')\n"
        "def lookup(name):\n"
        "    if name == 'pathlint_review_codec':\n"
        "        return codecs.CodecInfo(name=name, encode=lambda value, errors='strict': "
        "(BytePath(b'x'), len(value)), decode=None)\n"
        "codecs.register(lookup)\n"
        f"result = os.path.basename({factory}.{method}())\n"
    )
    assert fix_content(source) == (source, 0)
    # Isolate the codec registration from the test runner's codec registry.
    completed = subprocess.run(
        [sys.executable, "-c", source + "assert result == b'file'\n"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "argument",
    [
        "bytes('directory/file', 'utf-8').decode()",
        "'directory/file'.encode().decode()",
        "{b'path': 'directory/file'}[b'path']",
        "{b'path': 'directory/file' for _ in range(1)}[b'path']",
        "'directory/file' if b'condition' else 'other/file'",
        "'directory/file' if True else b'other/file'",
        "b'other/file' if False else 'directory/file'",
        "'directory/file' or b'other/file'",
        "b'' or 'directory/file'",
        "b'condition' and 'directory/file'",
        "b'directory/file'.decode()",
        "f'directory/{b\"file\"!s}'",
        "str(b'directory/file')",
        "repr(b'directory/file')",
        "ascii(b'directory/file')",
        "b'directory/file'.hex()",
        "'directory/%s' % b'file'",
        "chr(b'directory/file'[0]) + '/file'",
    ],
)
def test_text_expressions_with_bytes_or_opaque_calls_are_conservatively_skipped(
    argument: str,
) -> None:
    source = f"import os\nresult = os.path.basename({argument})\n"
    assert fix_content(source) == (source, 0)
    assert isinstance(run(source), str)


@pytest.mark.parametrize(("left", "parent"), [("7", "object"), ("'directory/%s'", "str")])
def test_reflected_modulo_byte_results_stay_unchanged(left: str, parent: str) -> None:
    source = (
        "import os\n"
        f"class Remainder({parent}):\n    def __rmod__(self, other):\n"
        "        return b'directory/file'\n"
        f"result = os.path.basename({left} % Remainder())\n"
    )
    assert fix_content(source) == (source, 0)
    assert run(source) == b"file"


@pytest.mark.parametrize(
    ("imports", "argument", "changes"),
    [
        ("from pathlib import Path", "Path('directory/file')", False),
        ("from pathlib import PurePath as P", "P('directory/file')", False),
        ("from os.path import join as connect", "connect('directory', 'file')", True),
        ("from os.path import splitext as split", "split('directory/file.txt')[0]", False),
        ("", "os.path.join('directory', 'file')", True),
        ("", "(os.path.join)('directory', 'file')", True),
    ],
)
def test_nested_calls_only_rewrite_literal_arguments(
    imports: str, argument: str, changes: bool
) -> None:
    source = f"import os\n{imports}\nresult = os.path.basename({argument})\n"
    updated, count = fix_content(source)
    assert bool(count) is changes
    assert "os.path.basename(" in updated
    assert run(updated) == run(source)
    assert fix_content(updated) == (updated, 0)


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
        '''f"é{((p.basename('a/b')))\t = !s}"''',
        '''f"""{(p.basename('a/b')\n) = }"""''',
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
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("gap", [" # keep comment\n)", " # } = !r are comment text\n)", " \\\n)"])
def test_debug_fstring_comment_before_equals_is_preserved(newline: str, gap: str) -> None:
    """Recognize a debug marker after a multiline grouping and comment."""
    original = (
        "import os.path as p\n" + '''result = f"""{(p.basename('a/b')''' + gap + '''=}"""\n'''
    ).replace("\n", newline)
    updated, count = fix_content(original)
    assert count == 0
    assert updated == original
    assert run(updated) == run(original)


@pytest.mark.parametrize(
    "expression",
    [
        '''f"value={p.basename('a/b')}"''',
        '''f"{p.basename('a/b')!r}"''',
        '''f"{p.basename('a/b') == 'b'}"''',
        '''f"{p.basename('a/b')=:{len(p.basename('c/d'))}}"''',
        pytest.param(
            '''f"""{(p.basename('a/b') # } = ignored\n)}"""''',
            marks=pytest.mark.skipif(
                sys.version_info < (3, 12), reason="F-string comments require Python 3.12"
            ),
        ),
    ],
)
def test_ordinary_fstring_fields_and_debug_format_specs_are_rewritten(expression: str) -> None:
    """Migrate ordinary fields and dynamic format specs while retaining debug labels."""
    original = f"import os.path as p\nresult = {expression}\n"
    updated, count = fix_content(original)
    assert count > 0
    assert "_Path(" in updated
    assert run(updated) == run(original)
    assert fix_content(updated) == (updated, 0)


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
    assert updated.count(newline) == original.count(newline) + 1
    assert "\n" not in updated.replace(newline, "")
    assert run(updated) == run(original)
    target = tmp_path / "unicode.py"
    with target.open("w", encoding="utf-8", newline="") as stream:
        stream.write(original)
    assert lint_file(target) == [
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


def test_cli_persists_valid_decorated_fix(tmp_path: Path, source_env: dict[str, str]) -> None:
    """Execute the persisted CLI fix for a decorated first definition."""
    source = (
        "from os.path import basename\n@lambda fn: fn\ndef check():\n"
        "    return basename('a/b')\nresult = check()\n"
    )
    target = tmp_path / "decorated.py"
    target.write_text(source, encoding="utf-8")
    process = subprocess.run(
        [sys.executable, "-m", "pathlint", "fix", str(target)],
        env=source_env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    persisted = target.read_text(encoding="utf-8")
    assert persisted == fix_content(source)[0]
    assert run(persisted) == run(source) == "b"


@pytest.mark.parametrize(
    ("original", "preserved"),
    [
        pytest.param(
            "from os.path import isabs as present, splitext\n"
            "result = (present('directory/file'), splitext('dir/file.ext'))\n",
            "from os.path import isabs as present, splitext",
            id="test_partial_named_import_keeps_unsupported_function",
        ),
        pytest.param(
            "from os.path import (\n    isabs,  # important comment\n)\n"
            "result = isabs('directory/file')\n",
            "# important comment",
            id="test_multiline_import_comments_are_preserved",
        ),
    ],
)
def test_fixed_sources_preserve_import_content(original: str, preserved: str) -> None:
    updated, count = fix_content(original)
    assert count == 1
    assert preserved in updated
    assert run(updated) == run(original)


def test_wildcard_import_is_not_deleted() -> None:
    original = "from os.path import *\nresult = isabs('file')\n"
    assert fix_content(original) == (original, 0)


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
    assert "from pathlib import Path as _Path\n" in updated


@pytest.mark.parametrize(
    ("preamble", "parameter", "alias"),
    [
        ("_Path = object()\n", "value", "_Path_"),
        ("_Path = object()\n_Path_ = object()\n", "value", "_Path__"),
        ("", "_Path", "_Path_"),
    ],
)
def test_generated_private_constructor_avoids_source_names(
    preamble: str, parameter: str, alias: str
) -> None:
    source = (
        preamble + "import os\n"
        f"def check({parameter}):\n"
        "    return os.path.basename('directory/file')\n"
        "result = check(object())\n"
    )
    updated, count = fix_content(source)
    assert count == 1
    assert f"from pathlib import Path as {alias}\n" in updated
    assert f"return {alias}('directory/file').name" in updated
    assert run(updated) == run(source) == "file"
    assert fix_content(updated) == (updated, 0)


@pytest.mark.parametrize(
    ("imports", "constructor"),
    [
        ("from pathlib import Path", "Path"),
        ("from pathlib import Path as P", "P"),
        ("from pathlib import Path as _P", "_P"),
        ("import pathlib", "pathlib.Path"),
        ("import pathlib as paths", "paths.Path"),
    ],
)
def test_existing_pathlib_constructor_alias_is_reused(imports: str, constructor: str) -> None:
    source = imports + "\nimport os\nresult = os.path.basename('directory/file')\n"
    updated, count = fix_content(source)
    assert count == 1
    assert f"result = {constructor}('directory/file').name" in updated
    assert "from pathlib import Path as _Path\n" not in updated
    assert run(updated) == run(source) == "file"


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
    ("header", "expected_doc"),
    [
        ("from os.path import basename\n'ordinary string'\n", None),
        (
            "from os.path import basename\nfrom os.path import dirname\n'ordinary string'\n",
            None,
        ),
        ("from os.path import basename; 'ordinary string'\n", None),
        (
            "'module documentation'\nfrom os.path import basename\n'ordinary string'\n",
            "module documentation",
        ),
        (
            "'module documentation'; from os.path import basename; 'ordinary string'\n",
            "module documentation",
        ),
        ("marker = True\nfrom os.path import basename\n'ordinary string'\n", None),
    ],
)
def test_import_cleanup_preserves_module_docstring(
    header: str, expected_doc: Optional[str], migrate_call: bool
) -> None:
    source = header + ("filename = basename('directory/file')\n" if migrate_call else "")
    source += "result = __doc__\n"
    updated, count = fix_content(source)
    assert "from os.path import basename" in updated
    if migrate_call:
        assert count > 0
        assert "filename = _Path(" in updated
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


@pytest.mark.parametrize(("alias", "target"), [("", "sep"), (" as separator", "separator")])
@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [
        ("", ""),
        ("if True:\n", ""),
        ("for _ in range(2):\n", ""),
        ("remaining = 2\nwhile remaining:\n", "    remaining -= 1\n"),
        ("def update():\n", "    return result\nresult = update()\n"),
    ],
)
def test_import_cleanup_preserves_augmented_assignment_reads(
    alias: str, target: str, prefix: str, suffix: str
) -> None:
    body = (
        f"from os.path import basename, sep{alias}\n"
        "result = basename('a/b')\n"
        f"{target} += 'suffix'\n"
    )
    source = prefix + (indent(body, "    ") if prefix else body) + suffix
    updated, count = fix_content(source)
    assert count > 0
    assert f"sep{alias}" in updated
    assert run(updated) == run(source)
    assert fix_content(updated) == (updated, 0)


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


@pytest.mark.parametrize("key", ["osp", "Path", "str"])
@pytest.mark.parametrize(
    ("imports", "accessor"),
    [
        ("import sys\n", "sys.modules[__name__]"),
        ("import sys as runtime\n", "runtime.modules[__name__]"),
        ("from sys import modules\n", "modules[__name__]"),
        ("from sys import modules as registry\n", "registry[__name__]"),
        ("import sys\nregistry = sys.modules\n", "registry[__name__]"),
        ("import importlib\n", "importlib.import_module(__name__)"),
        ("import importlib as loader\n", "loader.import_module(__name__)"),
        ("from importlib import import_module as load_module\n", "load_module(__name__)"),
    ],
)
def test_module_registry_writes_leave_source_unchanged(
    key: str, imports: str, accessor: str
) -> None:
    source = (
        "import os.path as osp\nfrom types import SimpleNamespace\n"
        "custom = SimpleNamespace(basename=lambda value: 'custom')\n"
        "Custom = lambda value: SimpleNamespace(name='custom')\n"
        + imports
        + f"{accessor}.{key} = "
        + {"osp": "custom", "Path": "Custom", "str": "repr"}[key]
        + "\nresult = "
        + ("osp.join('a', 'b')" if key == "str" else "osp.basename('a/b')")
        + "\n"
    )
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    name = "pathlint_module_registry_fixture"
    previous = sys.modules.get(name)
    try:
        for content in (source, updated):
            module = ModuleType(name)
            sys.modules[name] = module
            exec(compile(content, "<module-registry>", "exec"), module.__dict__)
            assert module.result == (
                "custom" if key == "osp" else str(Path("a") / "b") if key == "str" else "b"
            )
    finally:
        if previous is None:
            del sys.modules[name]
        else:
            sys.modules[name] = previous


@pytest.mark.parametrize(
    ("imports", "call", "export", "declaration", "changes"),
    [
        *[
            ("from os.path import basename", "basename", "basename", declaration, True)
            for declaration in [
                "",
                '__all__ = ["basename"]',
                '__all__ = list(("basename",))',
                "from export_names import __all__",
                "from export_names import exported as __all__\n__all__.append('basename')",
            ]
        ],
        ("import os.path as paths", "paths.basename", "paths", "", True),
        ("from os import path as paths", "paths.basename", "paths", "", True),
        ("import os.path", "os.path.basename", "os", "", True),
        (
            "if True:\n    from os.path import basename\n    value = basename('a/b')",
            "basename",
            "basename",
            "",
            True,
        ),
        (
            "try:\n    from os.path import basename\n    value = basename('a/b')\n"
            "except ImportError:\n    pass",
            "basename",
            "basename",
            "",
            True,
        ),
        (
            "def load():\n    global basename\n    from os.path import basename\nload()",
            "basename",
            "basename",
            "",
            False,
        ),
    ],
)
def test_import_cleanup_preserves_public_exports(
    tmp_path: Path, imports: str, call: str, export: str, declaration: str, changes: bool
) -> None:
    source = f"{imports}\n{declaration}\nresult = {call}('directory/file')\n"
    updated, count = fix_content(source)
    assert bool(count) is changes
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
                f"from fixture import {export}; "
                f"assert {call}('directory/file') == 'file'; "
                "Path = 'sentinel'; from fixture import *; assert Path == 'sentinel'; "
                f"print({call}('directory/file'))",
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
        *[
            (
                "from types import ModuleType\n"
                "class Custom(ModuleType):\n"
                "    @property\n    def basename(self):\n"
                "        return lambda value: 'custom'\n"
                + mutation
                + "\nresult = osp.basename('directory/file')\n",
                "custom",
                False,
            )
            for mutation in [
                "osp.__class__ = Custom",
                "osp.__class__: object = Custom",
                "alias = osp\nalias.__class__ = Custom",
                "def writer():\n    osp.__class__ = Custom\nwriter()",
            ]
        ],
        *[
            (
                "import os\nfrom types import ModuleType\n"
                "class Custom(ModuleType):\n"
                "    @property\n    def path(self):\n"
                "        return SimpleNamespace(basename=lambda value: 'custom')\n"
                + mutation
                + "\nresult = os.path.basename('directory/file')\n",
                "custom",
                False,
            )
            for mutation in ["os.__class__ = Custom", "alias = os\nalias.__class__ = Custom"]
        ],
        *[
            (
                "import pathlib as paths\nfrom types import ModuleType\n"
                "class Custom(ModuleType):\n"
                "    @property\n    def Path(self):\n"
                "        return lambda value: SimpleNamespace(name='custom')\n"
                + mutation
                + "\nresult = osp.basename('directory/file')\n",
                "file",
                False,
            )
            for mutation in ["paths.__class__ = Custom", "alias = paths\nalias.__class__ = Custom"]
        ],
        (
            "osp.basename = lambda value: 'custom'\nresult = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        *[
            (
                f"{binding}{accessor}('basename', lambda value: 'custom')\n"
                "result = osp.basename('directory/file')\n",
                "custom",
                False,
            )
            for binding, accessor in [
                ("", "osp.__setattr__"),
                ("mutate = osp.__setattr__\n", "mutate"),
                ("from os.path import __setattr__ as mutate\n", "mutate"),
            ]
        ],
        *[
            (
                f"{binding}{accessor}('basename')\n"
                "try:\n    result = osp.basename('directory/file')\n"
                "except AttributeError:\n    result = 'missing'\n",
                "missing",
                False,
            )
            for binding, accessor in [
                ("", "osp.__delattr__"),
                ("mutate = osp.__delattr__\n", "mutate"),
                ("from os.path import __delattr__ as mutate\n", "mutate"),
            ]
        ],
        *[
            (
                "def custom(value):\n    return 'custom'\n"
                f"{binding}{accessor}('__code__', custom.__code__)\n"
                "result = osp.basename('directory/file')\n",
                "custom",
                False,
            )
            for binding, accessor in [
                ("", "osp.basename.__setattr__"),
                ("mutate = osp.basename.__setattr__\n", "mutate"),
            ]
        ],
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
            False,
        ),
        (
            "osp.basename = lambda value: 'custom'\nresult = osp.dirname('directory/file')\n",
            "directory",
            True,
        ),
        (
            "def custom(value):\n    return 'custom'\n"
            "osp.basename.__code__ = custom.__code__\n"
            "result = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "import os\ndef custom(value):\n    return 'custom'\n"
            "os.path.basename.__code__ = custom.__code__\n"
            "result = os.path.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "from os.path import basename as present\n"
            "def custom(value):\n    return 'custom'\n"
            "present.__code__ = custom.__code__\n"
            "result = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "def custom(value):\n    return 'custom'\n"
            "osp.basename.__code__: object = custom.__code__\n"
            "from os.path import basename as present\n"
            "result = present('directory/file')\n",
            "custom",
            False,
        ),
        (
            "def custom(value):\n    return 'custom'\n"
            "def reader():\n    return osp.basename('directory/file')\n"
            "def writer():\n    osp.basename.__code__ = custom.__code__\n"
            "writer()\nresult = reader()\n",
            "custom",
            False,
        ),
        (
            "def custom(value):\n    return 'custom'\n"
            "osp.basename.__code__ = custom.__code__\n"
            "result = osp.dirname('directory/file')\n",
            "directory",
            True,
        ),
        (
            "osp.basename.__defaults__ = ('directory/file',)\n"
            "result = osp.basename('directory/file')\n",
            "file",
            False,
        ),
        (
            "del osp.basename.__defaults__\nresult = osp.basename('directory/file')\n",
            "file",
            False,
        ),
        (
            "osp.basename.__dict__ = {'label': 'custom'}\n"
            "result = osp.basename('directory/file')\n",
            "file",
            False,
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
        *[
            (
                copy + "\nalias.basename = lambda value: 'custom'\n"
                "result = osp.basename('directory/file')\n",
                "custom",
                False,
            )
            for copy in (
                *(form.replace("original", "osp") for form in ALIAS_COPY_FORMS),
                "from contextlib import nullcontext\nwith nullcontext(osp) as alias:\n    pass",
                "def copied():\n    return osp\nalias = copied()",
                "factory = lambda: osp\nalias = factory()",
                "def copies():\n    yield osp\nalias = next(copies())",
                "class Box:\n    module = osp\nalias = Box.module",
                "class Box:\n    import os.path as module\nalias = Box.module",
                *(
                    ["match osp:\n    case alias:\n        pass"]
                    if sys.version_info >= (3, 10)
                    else []
                ),
                "alias, second = (osp, 1) if True else (None, 2)",
                "box = SimpleNamespace()\nbox.module = osp\nalias = box.module",
                "box = {}\nbox['module'] = osp\nalias = box['module']",
                "flag = True\nalias = osp if flag else object()",
                "alias = osp or object()",
            )
        ],
        (
            "alias = osp\ndel alias.basename\n"
            "try:\n    result = osp.basename('directory/file')\n"
            "except AttributeError:\n    result = 'missing'\n",
            "missing",
            False,
        ),
        (
            "alias = osp.basename\ndef custom(value):\n    return 'custom'\n"
            "alias.__code__ = custom.__code__\n"
            "result = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "factory = lambda: osp.basename\nalias = factory()\n"
            "def custom(value):\n    return 'custom'\n"
            "alias.__code__ = custom.__code__\nresult = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        *[
            (
                definition + f"\n{access}.basename = lambda value: 'custom'\n"
                "result = osp.basename('directory/file')\n",
                "custom",
                False,
            )
            for definition, access in (
                ("def holder(alias=osp):\n    pass", "holder.__defaults__[0]"),
                ("def holder(*, alias=osp):\n    pass", "holder.__kwdefaults__['alias']"),
                ("holder = lambda alias=osp: None", "holder.__defaults__[0]"),
                ("def holder(alias: osp):\n    pass", "holder.__annotations__['alias']"),
                ("def holder() -> osp:\n    pass", "holder.__annotations__['return']"),
                ("value: osp", "__annotations__['value']"),
            )
        ],
        (
            "def writer():\n    alias.basename = lambda value: 'custom'\n"
            "def maker():\n    global alias\n    alias = osp\n"
            "maker()\nwriter()\nresult = osp.basename('directory/file')\n",
            "custom",
            False,
        ),
        *[
            (
                f"def writer({parameter}):\n    alias.basename = lambda value: 'custom'\n"
                "writer()\nresult = osp.basename('directory/file')\n",
                "custom",
                False,
            )
            for parameter in ("alias=osp", "*, alias=osp")
        ],
        (
            "import os as alias\nalias = alias.path\nalias = alias.basename\n"
            "def custom(value):\n    return 'custom'\n"
            "alias.__code__ = custom.__code__\nimport os\n"
            "result = os.path.basename('directory/file')\n",
            "custom",
            False,
        ),
        (
            "alias = osp\nalias.basename = lambda value: 'custom'\n"
            "result = osp.dirname('directory/file')\n",
            "directory",
            True,
        ),
    ],
)
def test_mutated_path_attributes_preserve_results(body: str, expected: str, changes: bool) -> None:
    source = (
        "import os.path as osp\nfrom types import SimpleNamespace\nflag = True\nother = object()\n"
        + body
    )
    updated, count = fix_content(source)
    assert bool(count) is changes
    # Isolate module mutations and restore before subprocess coverage shuts down.
    program = (
        "import os, sys, pathlib\n"
        "original_os_class = os.__class__\n"
        "original_path = os.path\noriginal_basename = original_path.basename\n"
        "original_path_class = original_path.__class__\n"
        "original_pathlib_class = pathlib.__class__\n"
        "original_code = original_basename.__code__\n"
        "original_defaults = original_basename.__defaults__\n"
        "original_dict = original_basename.__dict__.copy()\n"
        "namespace = {}\ntry:\n"
        "    exec(compile(sys.argv[1], '<fixture>', 'exec'), namespace)\n"
        "    print(namespace['result'])\nfinally:\n"
        "    os.__class__ = original_os_class\n"
        "    original_path.__class__ = original_path_class\n"
        "    pathlib.__class__ = original_pathlib_class\n"
        "    os.path = original_path\n    original_path.basename = original_basename\n"
        "    original_basename.__code__ = original_code\n"
        "    original_basename.__defaults__ = original_defaults\n"
        "    original_basename.__dict__ = original_dict\n"
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
    "annotation",
    [
        "os.path.basename: object\n",
        "def annotate():\n    os.path.basename: object\nannotate()\n",
    ],
)
def test_attribute_annotation_without_value_does_not_write(annotation: str) -> None:
    """An annotation-only attribute target must not veto a safe subsequent fix."""
    source = "import os\n" + annotation + "result = os.path.basename('a/b')\n"
    updated, count = fix_content(source)
    assert count == 1
    assert run(updated) == run(source) == "b"


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
        "alias = paths\nalias.Path = lambda value: None",
        "alias: object = paths.Path\nalias.exists = lambda self: True",
        "(alias := paths.PurePath)\nalias.name = property(lambda self: 'custom')",
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
