"""Behavioral regressions for AST-backed, source-preserving transformations."""

import ast
import subprocess
import sys
from pathlib import Path
from textwrap import indent

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
        ("import os", "os.path.exists(FILE)"),
        ("import os as operating_system", "operating_system.path.exists(FILE)"),
        ("import os.path as paths", "paths.exists(FILE)"),
        ("from os import path as paths", "paths.exists(FILE)"),
        ("from os.path import exists as present", "present(FILE)"),
        ("from os.path import isfile", "isfile(FILE)"),
        ("from os.path import isdir", "isdir(FILE)"),
        ("import os", "os.path.isabs(FILE)"),
        ("import os", "os.path.basename(FILE)"),
        ("import os", "os.path.dirname(FILE)"),
        ("import os", "os.path.expanduser(FILE)"),
        ("import os", "os.path.join('directory', 'file')"),
        ("import os", "os.path.basename(os.path.join('directory', 'file'))"),
        ("import pathlib\nimport os", "os.path.exists(FILE)"),
        ("import pathlib as paths\nimport os", "os.path.exists(FILE)"),
        ("from pathlib import Path as P\nimport os", "os.path.exists(FILE)"),
        ("import os.path as paths", "(paths.basename)('directory/file')"),
        ("import os", "((os.path.exists))(FILE)"),
        ("from os.path import exists as present", "(present)(FILE)"),
        ("import os", "(os.path.basename)((os.path.join)('directory', 'file'))"),
        ("import os", "(\n    (os.path.basename)  # callable (comment\n    ('directory/file')\n)"),
    ],
)
def test_supported_calls_preserve_results(tmp_path: Path, imports: str, expression: str) -> None:
    target = tmp_path / "file"
    target.write_text("content", encoding="utf-8")
    original = imports + "\nresult = " + expression.replace("FILE", repr(str(target))) + "\n"
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
        "os.path.exists(path='file')",
        "os.path.join('directory', *parts)",
        "os.path.exists(b'file')",
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


def test_strings_comments_and_fstring_text_survive(tmp_path: Path) -> None:
    original = (
        '"""os.path.exists("docstring")"""\n'
        "import os.path\n"
        "# os.path.exists('comment')\n"
        "literal = \"os.path.exists('literal')\"\n"
        f"result = f\"os.path.exists('text'): {{os.path.exists({str(tmp_path)!r})}}\"\n"
    )
    updated, count = fix_content(original)
    assert count > 0
    for text in (
        '"""os.path.exists("docstring")"""',
        "# os.path.exists('comment')",
        "literal = \"os.path.exists('literal')\"",
        "os.path.exists('text'): ",
    ):
        assert text in updated
    assert run(updated) == run(original)


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
        f"label = '🐍{separator}';\fresult = os.path.exists(\n    {str(tmp_path)!r},  # keep this comment\n)\n"
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
        (3, f"label = '🐍{separator}';\fresult = os.path.exists("),
    ]


def test_parenthesized_callable_comments_are_preserved(tmp_path: Path) -> None:
    original = (
        "import os.path as osp\n"
        "result = (\n"
        "    (  # before callable 🐍 (\n"
        "        osp.exists\n"
        "    )  # after callable (\n"
        f"    ({str(tmp_path)!r})\n"
        ")\n"
    )
    updated, count = fix_content(original)
    assert count > 0
    assert "# before callable 🐍 (" in updated
    assert "# after callable (" in updated
    assert run(updated) == run(original)


def test_import_follows_docstring_and_future_import(tmp_path: Path) -> None:
    original = (
        '#!/usr/bin/env python3\n# coding: utf-8\n"""Module documentation."""\n'
        "from __future__ import annotations\n"
        "import os.path\n"
        f"result = os.path.exists({str(tmp_path)!r})\n"
    )
    updated, _ = fix_content(original)
    assert updated.startswith(
        '#!/usr/bin/env python3\n# coding: utf-8\n"""Module documentation."""\n'
    )
    assert updated.index("from __future__") < updated.index("from pathlib")
    assert run(updated) == run(original)


def test_partial_named_import_keeps_unsupported_function(tmp_path: Path) -> None:
    original = (
        "from os.path import exists as present, splitext\n"
        f"result = (present({str(tmp_path)!r}), splitext('dir/file.ext'))\n"
    )
    updated, count = fix_content(original)
    assert count == 2
    assert "from os.path import splitext" in updated
    assert run(updated) == run(original)


def test_wildcard_import_is_not_deleted() -> None:
    original = "from os.path import *\nresult = exists('file')\n"
    assert fix_content(original) == (original, 0)


def test_multiline_import_comments_are_preserved(tmp_path: Path) -> None:
    original = (
        "from os.path import (\n    exists,  # important comment\n)\n"
        f"result = exists({str(tmp_path)!r})\n"
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
def test_constructor_does_not_collide_with_local_names(tmp_path: Path, preamble: str) -> None:
    original = (
        preamble + "import os\n"
        "def check(Path):\n"
        f"    return os.path.exists({str(tmp_path)!r})\n"
        "result = check(object())\n"
    )
    updated, count = fix_content(original)
    assert count > 0
    assert run(updated) == run(original)
    assert "from pathlib import Path as PathlintPath" in updated


@pytest.mark.parametrize(
    "source",
    [
        "def check():\n    return os.path.exists('missing')\nimport os\nresult = check()\n",
        "def outer():\n    def inner():\n        return p.exists('missing')\n    from os import path as p\n    return inner()\nresult = outer()\n",
    ],
)
def test_imports_defined_after_function_are_not_rewritten(source: str) -> None:
    assert fix_content(source) == (source, 0)
    assert run(source) is False


@pytest.mark.parametrize(
    "body",
    [
        "import os\n"
        "def check():\n    return os.path.exists('file')\n"
        "os = SimpleNamespace(path=custom)\nresult = check()\n",
        "import os.path as osp\n"
        "def check():\n    return osp.exists('file')\n"
        "osp = custom\nresult = check()\n",
        "osp = custom\n"
        "def check():\n    return osp.exists('file')\n"
        "result = check()\nimport os.path as osp\n",
        "import os.path as osp\n"
        "def check():\n    return osp.exists('file')\n"
        "osp = custom\nresult = check()\nimport os.path as osp\n",
        "def outer():\n"
        "    osp = custom\n"
        "    def check():\n        return osp.exists('file')\n"
        "    result = check()\n    import os.path as osp\n    return result\n"
        "result = outer()\n",
        "osp = custom\n"
        "check = lambda: osp.exists('file')\n"
        "result = check()\nimport os.path as osp\n",
        "osp = custom\n"
        "class Check:\n    def check(self):\n        return osp.exists('file')\n"
        "result = Check().check()\nimport os.path as osp\n",
        "osp = custom\n"
        "def invoke(function):\n    global result\n    result = function()\n    return function\n"
        "@invoke\ndef check():\n    return osp.exists('file')\n"
        "import os.path as osp\n",
        "import os.path as osp\n"
        "def writer():\n    global osp\n    osp = custom\n"
        "def check():\n    return osp.exists('file')\n"
        "writer()\nresult = check()\n",
        "def outer():\n    import os.path as osp\n"
        "    def writer():\n        nonlocal osp\n        osp = custom\n"
        "    def check():\n        return osp.exists('file')\n"
        "    writer()\n    return check()\n"
        "result = outer()\n",
    ],
)
def test_reassigned_enclosing_aliases_are_not_rewritten(body: str) -> None:
    source = (
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(exists=lambda value: 'custom')\n" + body
    )
    assert fix_content(source) == (source, 0)
    assert run(source) == "custom"


@pytest.mark.parametrize(
    "binding",
    [
        "str = lambda value: 'custom'",
        "from builtins import repr as str",
        "import builtins as str",
        "def writer():\n    global str\n    from builtins import repr as str\nwriter()",
    ],
)
def test_deferred_string_conversion_respects_later_builtin_shadowing(binding: str) -> None:
    source = (
        "import os.path as osp\n"
        "def check():\n    return osp.join('directory', 'file')\n"
        f"{binding}\n"
        "result = check()\n"
    )
    assert fix_content(source) == (source, 0)
    assert run(source) == str(Path("directory") / "file")


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
        "import os\ndef check():\n    return os.path.exists('missing')\nresult = check()\n",
        "from os.path import exists\n"
        "def check():\n    return exists('missing')\n"
        "result = check()\n",
        "import os.path as osp\ndef check():\n    return osp.exists('missing')\nresult = check()\n",
        "import os.path as osp\ncheck = lambda: osp.exists('missing')\nresult = check()\n",
        "def outer():\n    import os.path as osp\n"
        "    def check():\n        return osp.exists('missing')\n"
        "    return check()\n"
        "result = outer()\n",
        "from pathlib import Path as P\n"
        "import os.path as osp\n"
        "def check():\n    return osp.exists('missing')\n"
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
        "def check():\n    return osp.exists('missing')\n"
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
    (package / "custom.py").write_text("def exists(value): return 'custom'\n", encoding="utf-8")
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
        "import os.path; result = os.path.exists('missing')\n",
        "import sys; import os.path\nresult = os.path.exists('missing')\n",
        "def check():\n    import os.path\n    return os.path.exists('missing')\nresult = check()\n",
        "def check():\n    import os.path\nresult = check()\n",
        "import os.path as osp; result = osp.exists('missing'); del osp\n",
        "from os.path import exists as present\nresult = present('missing')\ndel present\n",
        "from os import path as paths\nresult = paths.exists('missing')\ndel paths\n",
        "import os.path\nresult = os.path.exists('missing')\ndel os\n",
        "import os\ndef outer():\n    from os.path import exists\n"
        "    def inner():\n        nonlocal exists\n        exists = None\n"
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
    ("definition", "lookup"),
    [
        ("def check(value: 'basename'): pass", "get_type_hints(check)['value'].__name__"),
        ("def check() -> 'basename': pass", "get_type_hints(check)['return'].__name__"),
        (
            "class Check:\n    value: 'basename'",
            "get_type_hints(Check, globalns=globals())['value'].__name__",
        ),
        (
            "def check(value: list['basename']): pass",
            "get_type_hints(check)['value'].__args__[0].__name__",
        ),
        (
            "Alias = 'basename'\ndef check(value: Alias): pass",
            "get_type_hints(check)['value'].__name__",
        ),
        (
            "def check(value): pass\ncheck.__annotations__ = {'value': 'basename'}",
            "get_type_hints(check)['value'].__name__",
        ),
        (
            "import types\n__annotations__ = {'value': 'basename'}\n"
            "module = types.ModuleType('fixture')\nmodule.__dict__.update(globals())",
            "get_type_hints(module)['value'].__name__",
        ),
    ],
)
def test_import_cleanup_preserves_quoted_annotations(
    definition: str, lookup: str, rewrite_call: bool
) -> None:
    source = (
        "from os.path import basename\nfrom typing import get_type_hints\n"
        + definition
        + f"\nresult = {lookup}\n"
        + ("filename = basename('directory/file')\n" if rewrite_call else "")
    )
    updated, count = fix_content(source)
    assert count == int(rewrite_call)
    assert "from os.path import basename" in updated
    assert run(updated) == run(source) == "basename"


def test_crlf_and_final_newline_are_preserved() -> None:
    original = "import os.path\r\nresult = os.path.exists('missing')\r\n"
    updated, count = fix_content(original)
    assert count > 0
    assert updated.endswith("\r\n")
    assert "\n" not in updated.replace("\r\n", "")
    assert run(updated) == run(original)


def test_invalid_source_is_not_transformed() -> None:
    with pytest.raises(SyntaxError):
        fix_content("import os\nos.path.exists(\n")


def test_result_for_missing_path_is_false() -> None:
    # Exercise both versions against the host without relying on mocked pathlib methods.
    original = "import os\nresult = os.path.exists('definitely-not-a-pathlint-test-file')\n"
    updated, _ = fix_content(original)
    assert run(original) is run(updated) is False


@pytest.mark.parametrize(
    "source",
    [
        "import os; result = os.path.exists('missing')\n",
        '"""Docstring."""; import os; result = os.path.exists("missing")\n',
        "from __future__ import annotations; import os; result = os.path.exists('missing')\n",
        "import os\n"
        "def check():\n    return os.path.exists('missing')\n"
        "result = check()\n"
        "from pathlib import Path\n",
        "import os\n"
        "def check():\n    return os.path.exists('missing')\n"
        "result = check()\n"
        "import pathlib\n",
        "import os\n"
        "try:\n    raise ValueError()\n"
        "except Exception as Path:\n    result = os.path.exists('missing')\n",
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
        "    os = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
        "else:\n"
        "    import os\n"
        "result = os.path.exists('missing')\n"
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
        "        result = os.path.exists('missing')\n"
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
        "            return os.path.exists('missing')\n"
        "result = check({'os': SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))})\n"
    )
    assert fix_content(source) == (source, 0)
    assert run(source) == "custom"


@pytest.mark.skipif(not hasattr(ast, "Match"), reason="Pattern matching requires Python 3.10")
@pytest.mark.parametrize(
    ("match", "expected", "changes"),
    [
        (
            "match custom:\n    case os if False:\n        pass\n"
            "    case _:\n        result = os.path.exists('missing')\n",
            "custom",
            False,
        ),
        (
            "match custom:\n    case os if False:\n        import os\n"
            "result = os.path.exists('missing')\n",
            "custom",
            False,
        ),
        (
            "match 1:\n    case _ if (os := custom) and False:\n        pass\n"
            "    case _:\n        result = os.path.exists('missing')\n",
            "custom",
            False,
        ),
        (
            "match 1:\n    case 0:\n        os = custom\n"
            "    case _:\n        result = os.path.exists('missing')\n",
            False,
            True,
        ),
    ],
)
def test_failed_match_guards_retain_bindings(match: str, expected: object, changes: bool) -> None:
    source = (
        "import os\nfrom types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n" + match
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
        "os = custom\ntry:\n    pass\nexcept Exception:\n    import os\nfinally:\n    result = os.path.exists('missing')\n",
        "import os\ntry:\n    os = custom\n    raise ValueError()\n    import os\n"
        "except ValueError:\n    result = os.path.exists('missing')\n",
        "import os\ntry:\n    try:\n        raise ValueError()\n"
        "    except ValueError:\n        os = custom\n        raise TypeError()\n        import os\n"
        "    finally:\n        result = os.path.exists('missing')\n"
        "except TypeError:\n    pass\n",
        "import os\ntry:\n    try:\n        pass\n"
        "    except ValueError:\n        pass\n"
        "    else:\n        os = custom\n        raise TypeError()\n        import os\n"
        "    finally:\n        result = os.path.exists('missing')\n"
        "except TypeError:\n    pass\n",
        "import os\n[(os := custom) for item in [0]]\n",
        "import os\n{(os := custom).path.exists('missing') for item in [0]}\n",
        "import os\n{item: (os := custom) for item in [0]}\n",
        "import os\n[[(os := custom) for item in [0]] for outer in [0]]\n",
        "import os\nsequence = ((os := custom) for item in [0])\nnext(sequence)\n",
        "def check():\n    import os\n    [(os := custom) for item in [0]]\n    return os\nos = check()\n",
        "import os\n[(os.path.exists('missing'), (os := custom)) for item in [0, 1]]\n",
    ],
)
def test_conditional_imports_do_not_override_custom_objects(block: str) -> None:
    source = (
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
        + block
        + "result = os.path.exists('missing')\n"
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
    ("values", "body", "alternative", "following", "expected", "changes"),
    [
        (
            "[]",
            "import os",
            "result = os.path.exists('missing')",
            "return result",
            "custom",
            False,
        ),
        (
            "[]",
            "pass",
            "import os\n        result = os.path.exists('missing')",
            "return result",
            False,
            True,
        ),
        (
            "[True]",
            "break",
            "import os",
            "return os.path.exists('missing')",
            "custom",
            False,
        ),
        (
            "[True]",
            "break\n        import os",
            "import os",
            "return os.path.exists('missing')",
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
        "    os = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
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
        "custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
        f"{loop}:\n"
        "    pass\n"
        "else:\n"
        "    result = os.path.exists('missing')\n"
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
            "results.append(alias.path.exists('missing'))\nalias = custom",
            "pass",
            "return results",
            [False, "custom"],
            False,
        ),
        (
            "import os\nfrom pathlib import Path as constructor\nresults = []",
            "results.append(os.path.exists('missing'))\n"
            "constructor = lambda value: SimpleNamespace(exists=lambda: 'custom')",
            "pass",
            "return results",
            [False, False],
            True,
        ),
        (
            "import os",
            "os = custom\nbreak\nimport os",
            "import os",
            "return os.path.exists('missing')",
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
        "    custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
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
        "custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
        "values = [True, True]\n"
        "result = 0\n"
        f"while alias.path.exists('missing') is False{binding_expression} "
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
        "custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
        "result = []\n"
        "for value in [1, 2]:\n"
        "    result.append(alias.path.exists('missing'))\n" + indent(definition + "\n", "    ")
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
