"""Regressions for import resolution and Python lexical scopes."""

from __future__ import annotations

import ast
import sys
from textwrap import dedent

import pytest

from pathlint.linter import OSPathDetector


def offenses(source: str) -> dict[int, set[str]]:
    """Return offending lines for a small source snippet."""
    source = dedent(source).strip()
    detector = OSPathDetector(source.splitlines())
    detector.visit(ast.parse(source))
    return dict(detector.offenses)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import os as system\nresult = system.path.exists('file')", {2}),
        ("import os.path as osp\nresult = osp.exists('file')", {1, 2}),
        ("from os import path as osp\nresult = osp.exists('file')", {1, 2}),
        ("from os.path import exists as present\nresult = present('file')", {1, 2}),
        ("from os.path import exists as present\ncallback = present", {1, 2}),
        ("import os\nmodule = os.path", {2}),
        ("import os.path\ncallback = os.path.join", {1, 2}),
        ("from os import path\nmodule = path", {1, 2}),
        ("import os\nvalue: os.path.exists = None", {2}),
        ("import os\ndef f(value: os.path.exists) -> os.path.join:\n    pass", {2}),
        ("import os\nasync def f():\n    return os.path.exists('file')", {3}),
        ("import os\ndef f():\n    return os.path.join('a', 'b')", {3}),
        ("import os\ndef f(value=os.path.exists('file')):\n    pass", {2}),
        ("import os\n@os.path.exists\ndef f():\n    pass", {2}),
    ],
)
def test_import_aliases_and_expression_contexts(source: str, expected: set[int]) -> None:
    assert set(offenses(source)) == expected


@pytest.mark.parametrize(
    "source",
    [
        "import custom_os.path\ncustom_os.path.exists('file')",
        "import os.pathlib\nos.pathlib.exists('file')",
        "from custom_os.path import exists\nexists('file')",
        "from os.pathlib import exists\nexists('file')",
        "from .os import path\npath.exists('file')",
        "from .os.path import exists\nexists('file')",
        "os.path.exists('file')",
        "import os\nos.getcwd()\nos.PathLike",
        "message = 'os.path.exists(value)'\n# os.path.exists(value)",
    ],
)
def test_only_actual_os_path_imports_are_resolved(source: str) -> None:
    assert offenses(source) == {}


@pytest.mark.parametrize(
    "source",
    [
        "import os\ndef f(os):\n    return os.path.exists('file')",
        "import os\ndef f(*os):\n    return os.path.exists('file')",
        "import os\ndef f(**os):\n    return os.path.exists('file')",
        "import os\ndef f():\n    os.path.exists('file')\n    os = object()",
        "import os\ndef f():\n    os.path.exists('file')\n    import other as os",
        "import os\ndef f():\n    os.path.exists('file')\n    def os():\n        pass",
        "import os\nos = object()\nos.path.exists('file')",
        "import os\nimport other as os\nos.path.exists('file')",
        "import os\ncallback = lambda os: os.path.exists('file')",
        "import os\ndef f():\n    try:\n        pass\n    except Exception as os:\n        os.path.exists('file')",
    ],
)
def test_shadowed_os_names_are_not_resolved(source: str) -> None:
    assert offenses(source) == {}


def test_shadowed_function_alias_keeps_import_diagnostic_only() -> None:
    source = """
        from os.path import exists as present
        def f(present):
            return present('file')
        present = lambda value: True
        present('file')
    """
    assert set(offenses(source)) == {1}


def test_function_import_does_not_escape_to_module() -> None:
    source = """
        def f():
            import os as system
            return system.path.exists('file')
        system.path.exists('file')
    """
    assert set(offenses(source)) == {3}


def test_nested_function_inherits_import_and_respects_own_arguments() -> None:
    source = """
        def outer():
            import os as system
            def imported():
                return system.path.exists('file')
            def shadowed(system):
                return system.path.exists('file')
    """
    assert set(offenses(source)) == {4}


def test_global_and_nonlocal_declarations_preserve_import_resolution() -> None:
    source = """
        import os
        def first():
            global os
            return os.path.exists('file')
        def outer():
            from os import path as osp
            def inner():
                nonlocal osp
                return osp.exists('file')
    """
    assert set(offenses(source)) == {4, 6, 9}


def test_class_assignment_does_not_shadow_global_inside_methods() -> None:
    source = """
        import os
        class Example:
            os = object()
            value = os.path.exists('file')
            def method(self):
                return os.path.exists('file')
    """
    assert set(offenses(source)) == {6}


def test_class_import_does_not_escape_into_methods() -> None:
    source = """
        class Example:
            import os
            value = os.path.exists('file')
            def method(self):
                return os.path.exists('file')
    """
    assert set(offenses(source)) == {3}


@pytest.mark.parametrize(
    "expression",
    [
        "[os.path.exists('file') for os in values]",
        "{os.path.exists('file') for os in values}",
        "{os.path.exists('file'): os for os in values}",
        "(os.path.exists('file') for os in values)",
    ],
)
def test_comprehension_targets_shadow_only_comprehension(expression: str) -> None:
    source = f"import os\nresult = {expression}\nos.path.exists('file')"
    assert set(offenses(source)) == {3}


def test_comprehension_outer_iterable_uses_outer_scope() -> None:
    source = "import os\nresult = [os.path for os in os.path.split('a/b')]"
    assert set(offenses(source)) == {2}


def test_comprehension_target_does_not_become_function_local() -> None:
    source = """
        import os
        def f(values):
            before = os.path.exists('file')
            result = [os.path for os in values]
            return os.path.exists('file')
    """
    assert set(offenses(source)) == {3, 5}


def test_lambda_argument_does_not_become_enclosing_function_local() -> None:
    source = """
        import os
        def f():
            callback = lambda os: os.path
            return os.path.exists('file')
    """
    assert set(offenses(source)) == {4}


def test_for_iterable_is_resolved_before_binding_target() -> None:
    source = """
        import os
        for os in os.path.split('a/b'):
            os.path.exists('file')
    """
    assert set(offenses(source)) == {2}


def test_multiple_usages_on_one_line_keep_exact_source_once() -> None:
    source = """
        from os.path import exists as present, join
        result = present(join('a', 'b')); callback = present
    """
    assert offenses(source) == {
        1: {"from os.path import exists as present, join"},
        2: {"result = present(join('a', 'b')); callback = present"},
    }


def test_function_resolves_import_defined_after_definition() -> None:
    source = """
        def f():
            return os.path.exists('file')
        import os
        result = f()
    """
    assert set(offenses(source)) == {2}


def test_function_respects_global_reassignment_before_call() -> None:
    source = """
        import os
        from types import SimpleNamespace
        def f():
            return os.path.exists('file')
        os = SimpleNamespace(path=SimpleNamespace(exists=lambda value: False))
        result = f()
    """
    assert offenses(source) == {}


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match requires Python 3.10")
@pytest.mark.parametrize("pattern", ["os", "{'os': os}", "[*os]", "{**os}"])
def test_match_captures_are_local_bindings(pattern: str) -> None:
    source = (
        "import os\n"
        "def f(value):\n"
        "    match value:\n"
        f"        case {pattern}:\n"
        "            return os.path.exists('file')\n"
    )
    assert offenses(source) == {}
