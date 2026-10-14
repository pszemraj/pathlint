"""Behavioral regressions for AST-backed, source-preserving transformations."""

import ast
from pathlib import Path
from textwrap import indent

import pytest

from pathlint.autofix import fix_content
from pathlint.linter import OSPathDetector


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


def test_unicode_offsets_and_multiline_comments(tmp_path: Path) -> None:
    original = (
        "import os.path\n"
        f"label = '🐍'; result = os.path.exists(\n    {str(tmp_path)!r},  # keep this comment\n)\n"
    )
    updated, count = fix_content(original)
    assert count > 0
    assert "label = '🐍';" in updated
    assert "# keep this comment" in updated
    assert run(updated) == run(original)


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
def test_imports_defined_after_function_are_resolved(source: str) -> None:
    updated, count = fix_content(source)
    assert count > 0
    assert run(updated) == run(source)


def test_reassigned_global_alias_is_not_rewritten() -> None:
    source = (
        "import os\n"
        "from types import SimpleNamespace\n"
        "def check():\n    return os.path.exists('file')\n"
        "os = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
        "result = check()\n"
    )
    assert fix_content(source) == (source, 0)
    assert run(source) == "custom"


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
    ],
)
def test_import_cleanup_leaves_valid_suites_and_semicolons(source: str) -> None:
    updated, count = fix_content(source)
    assert count > 0
    assert run(updated) == run(source)


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


@pytest.mark.parametrize(
    "block",
    [
        "try:\n    os = custom\nexcept Exception:\n    import os\n",
        "os = custom\nfor item in []:\n    import os\n",
        "os = custom\nwhile False:\n    import os\n",
        "os = custom\ntry:\n    pass\nexcept Exception:\n    import os\nfinally:\n    result = os.path.exists('missing')\n",
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
