"""Regressions for import resolution and Python lexical scopes."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from textwrap import dedent, indent
from types import ModuleType
from typing import get_type_hints

import pytest

from pathlint.autofix import fix_content
from pathlint.linter import OSPathDetector, lint_file
from tests._forms import ALIAS_COPY_FORMS
from tests.helpers import execute


def offenses(source: str) -> set[int]:
    """Return offending lines for a small source snippet."""
    source = dedent(source).strip()
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    return detector.offenses


@pytest.mark.parametrize("scope", ["module", "function", "class"])
@pytest.mark.parametrize(
    ("branches", "reported", "expected"),
    [
        ("if False:\n    os = custom\nresult = os.path.basename('a/b')", True, "b"),
        ("if True:\n    os = custom\nresult = os.path.basename('a/b')", False, "custom"),
        (
            "if flag:\n    os = custom\nelse:\n    result = os.path.basename('a/b')",
            True,
            "b",
        ),
        (
            "if flag:\n    os = custom\nelse:\n    import os\nresult = os.path.basename('a/b')",
            False,
            "b",
        ),
        (
            "if flag:\n    import os\nelse:\n    import os\nresult = os.path.basename('a/b')",
            True,
            "b",
        ),
        (
            "if flag:\n    import os\nelse:\n    os = custom\nresult = os.path.basename('a/b')",
            False,
            "custom",
        ),
        (
            "if False:\n    os = custom\nelif True:\n    import os\nelse:\n    os = custom\n"
            "result = os.path.basename('a/b')",
            True,
            "b",
        ),
        (
            "if False:\n    os = custom\nelse:\n    result = os.path.basename('a/b')",
            True,
            "b",
        ),
    ],
)
def test_conditional_bindings_are_analyzed_independently(
    scope: str, branches: str, reported: bool, expected: str, tmp_path: Path
) -> None:
    source = (
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(basename=lambda value: 'custom'))\n"
        "flag = False\n"
    )
    body = "import os\n" + branches + "\n"
    if scope == "function":
        source += "def check():\n" + indent(body + "return result\n", "    ")
        source += "observed = check()\n"
    elif scope == "class":
        source += "class Example:\n" + indent(body, "    ")
        source += "observed = Example.result\n"
    else:
        source += body + "observed = result\n"
    assert execute(source)["observed"] == expected
    target = tmp_path / "branches.py"
    target.write_text(source, encoding="utf-8")
    findings = lint_file(target)
    expected_lines = {
        index for index, line in enumerate(source.splitlines(), 1) if "result = os.path" in line
    }
    assert {line for line, _ in findings} == (expected_lines if reported else set())


@pytest.mark.parametrize(
    ("expression", "index", "expected", "changes"),
    [
        ("{'first': (os := custom), os.path.isabs('missing'): 0}", -1, "custom", False),
        ("{**((os := custom) and {}), os.path.isabs('missing'): 0}", 0, "custom", False),
        ("{os.path.isabs('missing'): 0, 'last': (os := custom)}", 0, False, True),
    ],
)
def test_dictionary_entries_follow_runtime_evaluation_order(
    expression: str, index: int, expected: object, changes: bool
) -> None:
    source = (
        "import os\nfrom types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        f"mapping = {expression}\nresult = list(mapping)[{index}]\n"
    )
    updated, count = fix_content(source)
    assert bool(count) is changes
    assert bool(offenses(source)) is changes
    for content in (source, updated):
        assert execute(content)["result"] == expected


@pytest.mark.parametrize(
    ("setup", "statement", "result", "expected", "changes"),
    [
        (
            "values = {custom: 0}",
            "values[(os := custom)] += os.path.isabs('missing')",
            "values[custom]",
            10,
            False,
        ),
        (
            "",
            "(os := custom).value += os.path.isabs('missing')",
            "custom.value",
            10,
            False,
        ),
        (
            "value = 0",
            "value += os.path.isabs('missing')",
            "value",
            0,
            True,
        ),
        (
            "values = {False: 0}",
            "values[os.path.isabs('missing')] += (os := custom).value + 10",
            "values[False]",
            10,
            True,
        ),
    ],
)
def test_augmented_targets_follow_runtime_evaluation_order(
    setup: str, statement: str, result: str, expected: int, changes: bool
) -> None:
    source = (
        "import os\nfrom types import SimpleNamespace\n"
        "class Custom:\n"
        "    path = SimpleNamespace(isabs=lambda value: 10)\n"
        "    value = 0\n"
        "custom = Custom()\n"
        f"{setup}\n{statement}\nresult = {result}\n"
    )
    updated, count = fix_content(source)
    assert bool(count) is changes
    assert bool(offenses(source)) is changes
    for content in (source, updated):
        assert execute(content)["result"] == expected


def test_augmented_name_binding_follows_its_value() -> None:
    assert offenses("import os\nos += os.path.exists('missing')") == {2}


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
    assert offenses(source) == expected


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
        "from pathlib import Path\nresult = Path('file').exists()",
        "message = 'os.path.exists(value)'\n# os.path.exists(value)",
    ],
)
def test_only_actual_os_path_imports_are_resolved(source: str) -> None:
    assert not offenses(source)


@pytest.mark.parametrize("loop", ["module", "for", "while"])
@pytest.mark.parametrize(
    ("imports", "name", "expression", "import_lines"),
    [
        ("import os", "os", "os.path.basename('a/b')", set()),
        ("import os as system", "system", "system.path.basename('a/b')", set()),
        ("import os.path as paths", "paths", "paths.basename('a/b')", {1}),
        ("from os import path as paths", "paths", "paths.basename('a/b')", {1}),
        ("from os.path import basename as base", "base", "base('a/b')", {1}),
    ],
)
def test_wildcard_import_invalidates_prior_aliases(
    imports: str, name: str, expression: str, import_lines: set[int], loop: str, tmp_path: Path
) -> None:
    plugin = (
        "from types import SimpleNamespace\n"
        "base = lambda value: 'custom'\n"
        "paths = SimpleNamespace(basename=base)\n"
        "os = SimpleNamespace(path=paths)\n"
        "system = os\n"
        f"__all__ = [{name!r}]\n"
    )
    (tmp_path / "plugin.py").write_text(plugin, encoding="utf-8")
    source = f"{imports}\n"
    if loop == "module":
        source += f"from plugin import *\nresult = {expression}\n"
    else:
        source += (
            "for _ in range(2):\n"
            if loop == "for"
            else "remaining = 2\nwhile remaining:\n    remaining -= 1\n"
        )
        source += indent(f"result = {expression}\nfrom plugin import *\n", "    ")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys\nsys.path.insert(0, {str(tmp_path)!r})\n" + source + "print(result)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "custom"
    assert offenses(source) == import_lines


@pytest.mark.parametrize(
    "scope",
    [
        "module",
        "function",
        "lambda",
        "method",
        "local-import",
        "class-global",
        "class-fallback",
    ],
)
@pytest.mark.parametrize("reimport", [False, True])
def test_wildcard_import_respects_deferred_scopes_and_later_imports(
    scope: str, reimport: bool
) -> None:
    source = "import os\nbefore = os.path.basename('a/b')\n"
    body = "return os.path.basename('a/b')\n"
    if scope == "function":
        source += "def check():\n" + indent(body, "    ")
    elif scope == "lambda":
        source += "check = lambda: os.path.basename('a/b')\n"
    elif scope == "method":
        source += "class Example:\n    def check(self):\n" + indent(body, "        ")
    elif scope == "local-import":
        source += "def check():\n    import os\n" + indent(body, "    ")
    elif scope in {"class-global", "class-fallback"}:
        source += "def check():\n    import os\n    class Example:\n"
        source += (
            "        global os\n"
            if scope == "class-global"
            else "        os = object()\n        del os\n"
        )
        source += "        result = os.path.basename('a/b')\n    return Example.result\n"
    source += "from plugin import *\n"
    if reimport:
        source += "import os\n"
    if scope == "module":
        source += "result = os.path.basename('a/b')\n"
    expected = {2}
    if reimport or scope == "local-import":
        expected.update(
            line
            for line, content in enumerate(source.splitlines(), 1)
            if "os.path.basename" in content
        )
    assert offenses(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from os.path import *", {1}),
        ("from os.path import *\nimport os\nos.path.basename('a/b')", {1, 3}),
        ("from .os.path import *", set()),
        ("from os import *", set()),
    ],
)
def test_wildcard_import_retains_absolute_os_path_diagnostic(
    source: str, expected: set[int]
) -> None:
    assert offenses(source) == expected


@pytest.mark.parametrize("wildcard_first", [False, True])
@pytest.mark.parametrize(
    ("expression", "deferred"),
    [
        ("(os.path.basename('a/b') for _ in [1])", True),
        ("(value for value in [os.path.basename('a/b')])", False),
        ("(value for _ in [1] for value in [os.path.basename('a/b')])", True),
        ("[os.path.basename('a/b') for _ in [1]]", False),
        ("{os.path.basename('a/b') for _ in [1]}", False),
        ("{os.path.basename('a/b'): _ for _ in [1]}", False),
    ],
)
def test_comprehension_bindings_follow_evaluation_time(
    expression: str, deferred: bool, wildcard_first: bool, tmp_path: Path
) -> None:
    (tmp_path / "plugin.py").write_text(
        "from types import SimpleNamespace\n"
        "__all__ = ['os']\n"
        "os = SimpleNamespace(path=SimpleNamespace(basename=lambda value: 'custom'))\n",
        encoding="utf-8",
    )
    source = "import os\n"
    if wildcard_first:
        source += "from plugin import *\n"
    source += f"values = {expression}\n"
    source += "import os\n" if wildcard_first else "from plugin import *\n"
    source += "result = list(values)[0]\n"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys\nsys.path.insert(0, {str(tmp_path)!r})\n" + source + "print(result)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    real_os = deferred == wildcard_first
    assert result.stdout.strip() == ("b" if real_os else "custom")
    assert offenses(source) == ({3 if wildcard_first else 2} if real_os else set())
    assert fix_content(source) == (source, 0)


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
        "import os\ntry:\n    raise ValueError()\nexcept ValueError as os:\n    import os\n"
        "os.path.exists('file')",
        "import os as __os\nclass Example:\n    result = __os.path.basename('a/b')",
    ],
)
def test_shadowed_os_names_are_not_resolved(source: str) -> None:
    assert not offenses(source)


@pytest.mark.parametrize("method", [False, True])
def test_private_alias_respects_class_name_mangling(method: bool) -> None:
    body = "result = __os.path.basename('a/b')\n"
    if method:
        body = "def check(self):\n    return __os.path.basename('a/b')\n"
    source = (
        "import os as __os\nfrom types import SimpleNamespace\n"
        "_Example__os = SimpleNamespace(path=SimpleNamespace(basename=lambda value: 'custom'))\n"
        "class Example:\n"
        + "\n".join("    " + line for line in body.splitlines())
        + ("\nresult = Example().check()\n" if method else "\nresult = Example.result\n")
    )
    assert not offenses(source)
    assert fix_content(source) == (source, 0)
    assert execute(source)["result"] == "custom"


def test_shadowed_function_alias_keeps_import_diagnostic_only() -> None:
    source = """
        from os.path import exists as present
        def f(present):
            return present('file')
        present = lambda value: True
        present('file')
    """
    assert offenses(source) == {1}


def test_function_import_does_not_escape_to_module() -> None:
    source = """
        def f():
            import os as system
            return system.path.exists('file')
        system.path.exists('file')
    """
    assert offenses(source) == {3}


def test_nested_function_inherits_import_and_respects_own_arguments() -> None:
    source = """
        def outer():
            import os as system
            def imported():
                return system.path.exists('file')
            def shadowed(system):
                return system.path.exists('file')
    """
    assert offenses(source) == {4}


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
    assert offenses(source) == {4, 6, 9}


@pytest.mark.parametrize("declaration", ["global", "nonlocal"])
@pytest.mark.parametrize("module_import", [False, True])
def test_nested_external_declaration_selects_its_namespace(
    declaration: str, module_import: bool
) -> None:
    custom = "os = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))"
    module_binding = "import os" if module_import else custom
    outer_binding = custom if module_import else "import os"
    source = (
        "from types import SimpleNamespace\n"
        f"{module_binding}\n"
        "def outer():\n"
        f"    {outer_binding}\n"
        "    def inner():\n"
        f"        {declaration} os\n"
        "        return os.path.isabs('unlikely-name')\n"
        "    return inner()\n"
        "result = outer()\n"
    )
    uses_import = module_import if declaration == "global" else not module_import
    assert offenses(source) == ({7} if uses_import else set())
    updated, _ = fix_content(source)
    for content in (source, updated):
        assert execute(content)["result"] == (False if uses_import else "custom")


@pytest.mark.parametrize("declaration", ["global", "nonlocal"])
@pytest.mark.parametrize(
    "statement",
    [
        "os = custom",
        "if False:\n    import os",
        "if True:\n    os = custom\nelse:\n    import os",
        "try:\n    os = custom\n    raise ValueError\n    import os\nexcept ValueError:\n    pass",
        "def os():\n    pass\nos.path = custom.path",
        "class os:\n    path = custom.path",
    ],
)
def test_immediate_class_external_writes_invalidate_owner(declaration: str, statement: str) -> None:
    source = (
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
    )
    body = (
        "os = custom\n"
        "class C:\n"
        f"    {declaration} os\n"
        + "\n".join("    " + line for line in statement.splitlines())
        + "\nresult = os.path.isabs('unlikely-name')\n"
    )
    if declaration == "nonlocal":
        body = (
            "def outer():\n"
            + "\n".join("    " + line for line in body.splitlines())
            + "\n    return result\nresult = outer()\n"
        )
    source += body
    updated, count = fix_content(source)
    assert count == 0
    assert not offenses(source)
    for content in (source, updated):
        assert execute(content)["result"] == "custom"


@pytest.mark.parametrize("declaration", ["global", "nonlocal"])
@pytest.mark.parametrize("statement", ["os = custom", "del os"])
def test_class_external_write_does_not_leave_import_visible(
    declaration: str, statement: str
) -> None:
    source = (
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
    )
    body = (
        "import os\n"
        "class C:\n"
        f"    {declaration} os\n"
        f"    {statement}\n"
        "    class Nested:\n"
        "        try:\n"
        "            result = os.path.isabs('unlikely-name')\n"
        "        except NameError:\n"
        "            result = 'deleted'\n"
        "try:\n"
        "    later = os.path.isabs('unlikely-name')\n"
        "except NameError:\n"
        "    later = 'deleted'\n"
        "result = (C.Nested.result, later)\n"
    )
    if declaration == "nonlocal":
        body = (
            "def outer():\n"
            + "\n".join("    " + line for line in body.splitlines())
            + "\n    return result\nresult = outer()\n"
        )
    source += body
    updated, count = fix_content(source)
    assert count == 0
    assert not offenses(source)
    for content in (source, updated):
        namespace = execute(content)
        expected = "deleted" if statement == "del os" else "custom"
        assert namespace["result"] == (expected, expected)


def test_class_assignment_does_not_shadow_global_inside_methods() -> None:
    source = """
        import os
        class Example:
            os = object()
            value = os.path.exists('file')
            def method(self):
                return os.path.exists('file')
    """
    assert offenses(source) == {6}


@pytest.mark.parametrize(
    ("imports", "name", "expression", "import_lines"),
    [
        ("import os", "os", "os.path.basename('a/b')", set()),
        ("import os as system", "system", "system.path.basename('a/b')", set()),
        ("from os import path as paths", "paths", "paths.basename('a/b')", {1}),
        ("from os.path import basename as base", "base", "base('a/b')", {1}),
    ],
)
@pytest.mark.parametrize("cleanup", ["delete", "exception"])
def test_class_deleted_binding_restores_global_import(
    imports: str, name: str, expression: str, import_lines: set[int], cleanup: str
) -> None:
    if cleanup == "delete":
        body = f"{name} = object()\ndel {name}\n"
    else:
        body = f"try:\n    raise ValueError\nexcept ValueError as {name}:\n    pass\n"
    source = imports + "\nclass Example:\n" + indent(body, "    ")
    source += f"    value = {expression}\nresult = Example.value\n"
    target_line = len(source.splitlines()) - 1
    assert offenses(source) == import_lines | {target_line}
    updated, count = fix_content(source)
    assert count == 1
    for content in (source, updated):
        assert execute(content)["result"] == "b"
    assert fix_content(updated) == (updated, 0)


@pytest.mark.parametrize(
    "cleanup",
    ["os = object()\ndel os", "try:\n    raise ValueError\nexcept ValueError as os:\n    pass"],
)
@pytest.mark.parametrize("global_import", [False, True])
def test_nested_class_deleted_binding_uses_globals(cleanup: str, global_import: bool) -> None:
    custom = "SimpleNamespace(path=SimpleNamespace(basename=lambda value: 'custom'))"
    source = "from types import SimpleNamespace\n"
    source += "import os\n" if global_import else f"os = {custom}\n"
    source += "def outer():\n"
    source += f"    os = {custom}\n" if global_import else "    import os\n"
    source += "    class Example:\n" + indent(cleanup + "\n", "        ")
    source += "        value = os.path.basename('a/b')\n    return Example.value\n"
    source += "result = outer()\n"
    target_line = len(source.splitlines()) - 2
    assert offenses(source) == ({target_line} if global_import else set())
    updated, _ = fix_content(source)
    for content in (source, updated):
        assert execute(content)["result"] == ("b" if global_import else "custom")


@pytest.mark.parametrize("global_import", [False, True])
def test_class_missing_locals_and_methods_select_their_namespaces(global_import: bool) -> None:
    custom = "SimpleNamespace(path=SimpleNamespace(basename=lambda value: 'custom'))"
    source = "from types import SimpleNamespace\n"
    source += "import os\n" if global_import else f"os = {custom}\n"
    source += f"custom = {custom}\ndef outer():\n"
    source += "    os = custom\n" if global_import else "    import os\n"
    source += (
        "    class Example:\n"
        "        before = os.path.basename('a/b')\n"
        "        os = object()\n"
        "        del os\n"
        "        after = os.path.basename('a/b')\n"
        "        import os\n"
        "        imported = os.path.basename('a/b')\n"
        "        os = custom\n"
        "        rebound = os.path.basename('a/b')\n"
        "        def method(self):\n"
        "            return os.path.basename('a/b')\n"
        "    return (Example.before, Example.after, Example.imported, Example.rebound, Example().method())\n"
        "result = outer()\n"
    )
    assert offenses(source) == ({7, 10, 12} if global_import else {12, 16})
    updated, _ = fix_content(source)
    expected = (
        ("b", "b", "b", "custom", "custom")
        if global_import
        else ("custom", "custom", "b", "custom", "b")
    )
    for content in (source, updated):
        assert execute(content)["result"] == expected


@pytest.mark.parametrize("scope", ["module", "function", "class-global", "class-nonlocal"])
@pytest.mark.parametrize(
    "cleanup",
    ["os = object()\ndel os", "try:\n    raise ValueError\nexcept ValueError as os:\n    pass"],
)
def test_other_scope_deletions_do_not_restore_imports(scope: str, cleanup: str) -> None:
    body = (
        cleanup
        + "\ntry:\n    value = os.path.basename('a/b')\nexcept NameError:\n    value = 'deleted'\n"
    )
    source = "import os\n"
    if scope == "module":
        source += body + "result = value\n"
    elif scope == "function":
        source += "def check():\n" + indent(body + "return value\n", "    ")
        source += "result = check()\n"
    elif scope == "class-global":
        source += "class Example:\n    global os\n" + indent(body, "    ")
        source += "result = Example.value\n"
    else:
        source += "def outer():\n    import os\n    class Example:\n        nonlocal os\n"
        source += indent(body, "        ")
        source += "    return Example.value\nresult = outer()\n"
    assert not offenses(source)
    assert fix_content(source) == (source, 0)
    assert execute(source)["result"] == "deleted"


def test_class_import_does_not_escape_into_methods() -> None:
    source = """
        class Example:
            import os
            value = os.path.exists('file')
            def method(self):
                return os.path.exists('file')
    """
    assert offenses(source) == {3}


def test_class_name_is_bound_after_its_body() -> None:
    source = "import os\nclass os:\n    result = os.path.exists('file')"
    assert offenses(source) == {3}


@pytest.mark.parametrize("target", ["os", "os.path.exists", "os.path.__dict__['exists']"])
def test_annotation_only_target_does_not_assign(target: str) -> None:
    source = f"import os\n{target}: object\nos.path.exists('file')"
    assert 3 in offenses(source)


def test_annotation_only_function_name_remains_local() -> None:
    source = "import os\ndef f():\n    os: object\n    os.path.exists('file')"
    assert not offenses(source)


def test_annotated_assignment_binds_before_annotation() -> None:
    source = (
        "import os\n"
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        "os: os.path.isabs('file') = custom\n"
        "result = __annotations__['os']\n"
    )
    assert not offenses(source)
    updated, count = fix_content(source)
    assert count == 0
    for content in (source, updated):
        assert execute(content)["result"] == "custom"


@pytest.mark.parametrize("future", ["", "from __future__ import annotations\n"])
@pytest.mark.parametrize(
    ("statement", "owner", "key"),
    [
        ("value: os.path.isabs('file')", None, "value"),
        ("class Example:\n    value: os.path.isabs('file')", "Example", "value"),
        ("def f(arg: os.path.isabs('file')): pass", "f", "arg"),
        ("async def f(arg: os.path.isabs('file')): pass", "f", "arg"),
        ("def f() -> os.path.isabs('file'): pass", "f", "return"),
    ],
)
def test_annotation_fixes_preserve_later_type_hint_evaluation(
    future: str, statement: str, owner: str | None, key: str
) -> None:
    source = (
        f"{future}import os\nfrom types import SimpleNamespace\n{statement}\n"
        "os = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: int))\n"
    )
    assert offenses(source)
    updated, count = fix_content(source)
    assert (updated, count) == (source, 0)
    for content in (source, updated):
        module = ModuleType("annotation_sample")
        exec(compile(content, "<annotations>", "exec", dont_inherit=True), vars(module))
        target = getattr(module, owner) if owner else module
        expected = int if future or sys.version_info >= (3, 14) else False
        assert get_type_hints(target, globalns=vars(module)) == {key: expected}


@pytest.mark.parametrize("future", ["", "from __future__ import annotations\n"])
@pytest.mark.parametrize(
    "statement",
    [
        "value: os.path.isabs('file') = os.path.isabs('file')",
        "class Example:\n    value: os.path.isabs('file') = os.path.isabs('file')",
        "obj.value: os.path.isabs('file') = os.path.isabs('file')",
        "values[0]: os.path.isabs('file') = os.path.isabs('file')",
        "def f(arg: os.path.isabs('file') = os.path.isabs('file')): pass",
        "def f(arg: os.path.isabs('file'), /, *, kw: os.path.isabs('file')):\n"
        "    return os.path.isabs('file')",
        "def f(*args: os.path.isabs('file'), **kwargs: os.path.isabs('file')):\n"
        "    return os.path.isabs('file')",
        "def f() -> os.path.isabs('file'):\n    return os.path.isabs('file')",
        "def f():\n    value: os.path.isabs('file') = os.path.isabs('file')",
    ],
)
def test_annotations_stay_intact_while_ordinary_calls_are_fixed(
    future: str, statement: str
) -> None:
    source = f"{future}import os\n{statement}\n"
    updated, count = fix_content(source)
    assert count == 1
    assert updated.count("os.path.isabs('file')") == source.count("os.path.isabs('file')") - 1
    assert "Path('file').is_absolute()" in updated
    annotations = []
    for content in (source, updated):
        annotations.append(
            [
                ast.dump(annotation)
                for node in ast.walk(ast.parse(content))
                for annotation in (
                    getattr(node, "annotation", None),
                    getattr(node, "returns", None),
                )
                if annotation is not None
            ]
        )
    assert annotations[0] == annotations[1]
    assert offenses(updated)


@pytest.mark.parametrize("definition", ["def", "async def"])
@pytest.mark.parametrize("parameter", ["arg", "*, arg"])
def test_function_defaults_bind_before_annotations(definition: str, parameter: str) -> None:
    source = (
        "import os\n"
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        f"{definition} f({parameter}: os.path.isabs('unlikely-name') = (os := custom)):\n"
        "    pass\n"
        "result = f.__annotations__['arg']\n"
    )
    assert not offenses(source)
    updated, count = fix_content(source)
    assert count == 0
    for content in (source, updated):
        assert execute(content)["result"] == "custom"


@pytest.mark.parametrize("definition", ["def", "async def"])
def test_vararg_annotation_binds_before_keyword_only_annotations(definition: str) -> None:
    source = (
        "import os\n"
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(isabs=lambda value: 'custom'))\n"
        f"{definition} f(\n"
        "    pos: os.path.isabs('file'), /, arg: os.path.isabs('file'),\n"
        "    *args: (os := custom), key: os.path.isabs('file'),\n"
        "    **kwargs: os.path.isabs('file'),\n"
        ") -> os.path.isabs('file'):\n    pass\n"
        "result = f.__annotations__\n"
    )
    assert offenses(source) == {5}
    assert fix_content(source) == (source, 0)
    annotations = execute(source)["result"]
    assert annotations["pos"] is False
    assert annotations["arg"] is False
    assert annotations["key"] == annotations["kwargs"] == annotations["return"] == "custom"


@pytest.mark.parametrize("parameter", ["arg=(os := os)", "arg: (os := os) = None"])
def test_definition_walrus_does_not_become_function_local(parameter: str) -> None:
    source = (
        "import os\n"
        f"def f({parameter}):\n"
        "    return os.path.isabs('unlikely-name')\n"
        "import os\n"
        "result = f()\n"
    )
    assert offenses(source) == {3}
    assert fix_content(source) == (source, 0)
    assert execute(source)["result"] is False


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
    assert offenses(source) == {3}


def test_comprehension_outer_iterable_uses_outer_scope() -> None:
    source = "import os\nresult = [os.path for os in os.path.split('a/b')]"
    assert offenses(source) == {2}


def test_comprehension_target_does_not_become_function_local() -> None:
    source = """
        import os
        def f(values):
            before = os.path.exists('file')
            result = [os.path for os in values]
            return os.path.exists('file')
    """
    assert offenses(source) == {3, 5}


@pytest.mark.parametrize(
    "expression",
    [
        "[(alias := custom) for _ in [0]]",
        "{(alias := custom) for _ in [0]}",
        "{(alias := custom): 0 for _ in [0]}",
        "{0: (alias := custom) for _ in [0]}",
        "[0 for _ in [0] if (alias := custom)]",
        "[[(alias := custom) for _ in [0]] for _ in [0]]",
        "[(alias := custom) for _ in []]",
        "((alias := custom) for _ in [0])",
    ],
)
@pytest.mark.parametrize("in_function", [False, True])
def test_comprehension_walrus_invalidates_containing_binding(
    expression: str, in_function: bool
) -> None:
    body = f"import os as alias\nresult = {expression}\nalias.path.exists('file')"
    if in_function:
        body = "def f(custom):\n" + "\n".join("    " + line for line in body.splitlines())
    assert not offenses(body)


def test_comprehension_walrus_invalidates_calls_on_later_iterations() -> None:
    source = """
        import os as alias
        result = [(alias.path.exists('file'), (alias := custom)) for _ in [0, 1]]
    """
    assert not offenses(source)


def test_lambda_argument_does_not_become_enclosing_function_local() -> None:
    source = """
        import os
        def f():
            callback = lambda os: os.path
            return os.path.exists('file')
    """
    assert offenses(source) == {4}


def test_for_iterable_is_resolved_before_binding_target() -> None:
    source = """
        import os
        for os in os.path.split('a/b'):
            os.path.exists('file')
    """
    assert offenses(source) == {2}


@pytest.mark.parametrize("loop", ["for _ in values", "async for _ in values", "while flag"])
@pytest.mark.parametrize(
    ("before", "body", "alternative"),
    [
        ("import os", "os = custom", "pass"),
        ("os = custom", "import os", "pass"),
        ("os = custom", "break", "import os"),
        ("import os", "os = custom", "import os"),
    ],
)
def test_loop_exits_retain_only_certain_bindings(
    loop: str, before: str, body: str, alternative: str
) -> None:
    source = (
        f"{before}\n{loop}:\n    {body}\nelse:\n    {alternative}\nresult = os.path.basename('a/b')"
    )
    if loop.startswith("async"):
        source = "async def f(custom, values):\n" + indent(source, "    ")
    assert not offenses(source)


@pytest.mark.parametrize("loop", ["for _ in values", "async for _ in values", "while flag"])
def test_repeated_loop_bindings_do_not_resolve_earlier_calls(loop: str) -> None:
    source = (
        f"import os\n{loop}:\n"
        "    os.path.basename('a/b')\n    os = custom\n"
        "else:\n    os.path.basename('a/b')"
    )
    if loop.startswith("async"):
        source = "async def f(custom, values):\n" + indent(source, "    ")
    assert not offenses(source)


def test_while_test_respects_bindings_from_later_iterations() -> None:
    source = "import os\nwhile os.path.basename('a/b'):\n    os = custom"
    assert not offenses(source)


@pytest.mark.parametrize("initial", ["import os", "os = custom"])
def test_literal_false_loop_does_not_establish_body_bindings(initial: str) -> None:
    source = (
        f"{initial}\nwhile False:\n    import os\n"
        "else:\n    os = custom\nresult = os.path.basename('a/b')"
    )
    assert not offenses(source)


@pytest.mark.parametrize("loop", ["for _ in values", "async for _ in values", "while flag"])
def test_stable_loop_imports_remain_detectable_in_each_suite(loop: str) -> None:
    source = (
        f"import os\n{loop}:\n    os.path.basename('a/b')\n"
        "else:\n    os.path.basename('a/b')\nos.path.basename('a/b')"
    )
    expected = {3, 5, 6}
    if loop.startswith("async"):
        source = "async def f(values):\n" + indent(source, "    ")
        expected = {line + 1 for line in expected}
    assert offenses(source) == expected


@pytest.mark.parametrize(
    "loop",
    [
        "for _ in [1]:\n    break\nelse:\n    import os",
        "while True:\n    break\nelse:\n    import os",
        "for _ in []:\n    import os",
        "while False:\n    import os",
        "async for _ in values():\n    break\nelse:\n    import os",
    ],
)
def test_loop_false_positive_reproducer_executes_custom_binding(loop: str) -> None:
    header = (
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(basename=lambda value: 'custom'))\n"
    )
    body = f"os = custom\n{loop}\nresult = os.path.basename('a/b')"
    if loop.startswith("async"):
        source = header + "import asyncio\nasync def values():\n    yield 1\nasync def run():\n"
        source += indent(body + "\nreturn result", "    ")
        source += "\nresult = asyncio.run(run())"
    else:
        source = header + body
    namespace: dict[str, object] = {}
    exec(source, namespace)
    assert namespace["result"] == "custom"
    assert not offenses(source)


def test_multiple_usages_on_one_line_keep_exact_source_once(tmp_path: Path) -> None:
    source = dedent("""
        from os.path import exists as present, join
        result = present(join('a', 'b')); callback = present
    """).strip()
    assert offenses(source) == {1, 2}
    target = tmp_path / "multiple.py"
    target.write_text(source, encoding="utf-8")
    assert lint_file(target) == [
        (1, "from os.path import exists as present, join"),
        (2, "result = present(join('a', 'b')); callback = present"),
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import os\nprint(os.path.exists('test'))\n", [(2, "print(os.path.exists('test'))")]),
        (
            "from os import path as osp\nresult = osp.exists('file')\n",
            [(1, "from os import path as osp"), (2, "result = osp.exists('file')")],
        ),
        (
            "import os\nmarker = 'line\vbreak'\nresult = os.path.basename('a/b')\n",
            [(3, "result = os.path.basename('a/b')")],
        ),
        ("import ntpath\nresult = ntpath.basename('a/b')\n", []),
        ("import posixpath\nresult = posixpath.basename('a/b')\n", []),
    ],
)
def test_lint_file_preserves_findings_and_physical_lines(
    tmp_path: Path, source: str, expected: list[tuple[int, str]]
) -> None:
    target = tmp_path / "source.py"
    target.write_text(source, encoding="utf-8")
    assert lint_file(target) == expected


def test_lint_file_parses_before_skipping_irrelevant_source(tmp_path: Path) -> None:
    target = tmp_path / "invalid.py"
    target.write_text("if True\n    pass\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        lint_file(target)


@pytest.mark.parametrize(
    "mutation",
    [
        "osp.basename = custom",
        "osp.basename: object = custom",
        "osp.basename += custom",
        "del osp.basename",
    ],
)
def test_attribute_mutations_invalidate_all_aliases_and_reimports(mutation: str) -> None:
    source = (
        "import os.path as osp\n"
        "import os as system\n"
        "from os import path as other\n"
        f"{mutation}\n"
        "import os.path as imported_again\n"
        "from os.path import basename as named_again\n"
    )
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {"os.path.basename"}
    for expression in (
        "osp.basename",
        "system.path.basename",
        "other.basename",
        "imported_again.basename",
        "named_again",
    ):
        assert detector.resolve(ast.parse(expression, mode="eval").body) is None
    assert detector.resolve(ast.parse("osp.dirname", mode="eval").body) == "os.path.dirname"


def test_replacing_path_module_invalidates_descendants() -> None:
    source = "import os\nfrom os import path as osp\nos.path = custom\nimport os as system"
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {"os.path"}
    for expression in ("osp", "osp.basename", "os.path.exists", "system.path.dirname"):
        assert detector.resolve(ast.parse(expression, mode="eval").body) is None


@pytest.mark.parametrize(
    ("import_statement", "target", "changed"),
    [
        ("import os as module", "module", "os"),
        ("import os as module", "module.path", "os.path"),
        ("import os.path as module", "module", "os.path"),
        ("from os import path as module", "module", "os.path"),
        ("import pathlib as module", "module", "pathlib"),
    ],
)
@pytest.mark.parametrize(
    "mutation",
    [
        "{target}.__class__ = Custom",
        "{target}.__class__: object = Custom",
        "{target}.__class__ += Custom",
        "del {target}.__class__",
    ],
)
def test_module_class_mutations_invalidate_sibling_lookups(
    import_statement: str, target: str, changed: str, mutation: str
) -> None:
    source = (
        f"{import_statement}\n{mutation.format(target=target)}\n"
        "import os.path as osp\nfrom os.path import basename\n"
        "import pathlib as paths\nfrom pathlib import Path\n"
    )
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {changed}
    affected = (
        ("paths", "paths.Path", "Path")
        if changed == "pathlib"
        else ("osp", "osp.basename", "osp.dirname", "basename")
    )
    for expression in affected:
        assert detector.resolve(ast.parse(expression, mode="eval").body) is None
    unaffected = "osp.dirname" if changed == "pathlib" else "paths.Path"
    expected = "os.path.dirname" if changed == "pathlib" else "pathlib.Path"
    assert detector.resolve(ast.parse(unaffected, mode="eval").body) == expected


@pytest.mark.parametrize(
    ("import_statement", "changed", "expression"),
    [
        ("import os as module", "os", "os.path.basename"),
        ("import os.path as module", "os.path", "os.path.basename"),
        ("import pathlib as module", "pathlib", "paths.Path"),
    ],
)
@pytest.mark.parametrize(
    "copy",
    [
        "copy = module\ncopy.__class__ = Custom",
        "def mutate(copy=module):\n    copy.__class__ = Custom",
    ],
)
def test_copied_module_class_mutations_retain_whole_module_origins(
    import_statement: str, changed: str, expression: str, copy: str
) -> None:
    source = f"{import_statement}\n{copy}\nimport os\nimport pathlib as paths\n"
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {changed}
    assert detector.resolve(ast.parse(expression, mode="eval").body) is None
    assert detector.resolve(ast.parse("copy", mode="eval").body) is None


@pytest.mark.parametrize(
    ("import_statement", "target"),
    [
        ("import os", "os.path.basename"),
        ("import os.path as osp", "osp.basename"),
        ("from os import path as osp", "osp.basename"),
        ("from os.path import basename as present", "present"),
    ],
)
@pytest.mark.parametrize(
    ("statement", "attribute"),
    [
        ("{target}.__code__ = custom.__code__", "__code__"),
        ("{target}.__defaults__ = defaults", "__defaults__"),
        ("{target}.__defaults__ += defaults", "__defaults__"),
        ("del {target}.__defaults__", "__defaults__"),
        ("{target}.__dict__ = namespace", "__dict__"),
        ("{target}.__class__ = Custom", "__class__"),
    ],
)
def test_callable_mutations_invalidate_function_without_invalidating_module(
    import_statement: str, target: str, statement: str, attribute: str
) -> None:
    source = (
        import_statement + "\n" + statement.format(target=target) + "\n"
        "import os.path as other\nfrom os.path import basename as named_again\n"
    )
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {f"os.path.basename.{attribute}"}
    for expression in (target, "other.basename", "named_again"):
        assert detector.resolve(ast.parse(expression, mode="eval").body) is None
    assert detector.resolve(ast.parse("other", mode="eval").body) == "os.path"
    assert detector.resolve(ast.parse("other.dirname", mode="eval").body) == "os.path.dirname"


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("module.Path = custom", "pathlib.Path"),
        ("del module.Path", "pathlib.Path"),
        ("constructor.exists = custom", "pathlib.Path.exists"),
        ("del constructor.exists", "pathlib.Path.exists"),
        ("module.PurePath.name = custom", "pathlib.PurePath.name"),
        ("module.PosixPath.exists = custom", "pathlib.PosixPath.exists"),
    ],
)
def test_pathlib_mutations_invalidate_constructor_origins(statement: str, expected: str) -> None:
    source = "import pathlib as module\nfrom pathlib import Path as constructor\n" + statement
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {expected}
    assert (
        detector.resolve(
            ast.parse(statement.removeprefix("del ").split(" = ")[0], mode="eval").body
        )
        is None
    )
    if expected.startswith("pathlib.Path"):
        assert detector.resolve(ast.parse("constructor", mode="eval").body) is None
        assert detector.resolve(ast.parse("constructor.exists", mode="eval").body) is None
    if expected == "pathlib.Path":
        assert detector.resolve(ast.parse("module.Path", mode="eval").body) is None


def test_deferred_method_mutations_are_collected() -> None:
    source = """
        import os.path as osp
        def reader():
            return osp.basename('directory/file')
        def writer():
            osp.basename = custom
        writer()
        result = reader()
    """
    source = dedent(source).strip()
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {"os.path.basename"}


@pytest.mark.parametrize(
    "copy",
    [
        *ALIAS_COPY_FORMS,
        "def writer(alias=original):\n    pass",
        "def writer(*, alias=original):\n    pass",
    ],
)
@pytest.mark.parametrize(
    ("original", "mutation", "changed"),
    [
        ("osp", "alias.basename = custom", "os.path.basename"),
        ("osp.basename", "alias.__code__ = custom.__code__", "os.path.basename.__code__"),
        ("paths.Path", "alias.exists = custom", "pathlib.Path.exists"),
    ],
)
def test_copied_alias_mutations_do_not_establish_read_bindings(
    copy: str, original: str, mutation: str, changed: str
) -> None:
    source = (
        "import os.path as osp\nimport pathlib as paths\nflag = True\nother = object()\n"
        + copy.replace("original", original)
        + "\n"
        + mutation
    )
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert changed in detector.mutated_attributes
    assert detector.resolve(ast.parse("alias", mode="eval").body) is None
    assert detector.resolve(ast.parse("osp.dirname", mode="eval").body) == "os.path.dirname"


@pytest.mark.parametrize(
    "copy",
    [
        "box.module = original",
        "box['module'] = original",
        "from contextlib import nullcontext\nwith nullcontext(original) as alias:\n    pass",
        "def copied():\n    return original\nalias = copied()",
        "factory = lambda: original\nalias = factory()",
        "def copies():\n    yield original\nalias = next(copies())",
        "class Box:\n    module = original",
        pytest.param(
            "match original:\n    case alias:\n        pass",
            marks=pytest.mark.skipif(
                sys.version_info < (3, 10), reason="match requires Python 3.10"
            ),
        ),
    ],
)
@pytest.mark.parametrize(
    ("original", "escaped"),
    [("osp", "os.path"), ("osp.basename", "os.path.basename"), ("paths.Path", "pathlib.Path")],
)
def test_escaped_namespace_copies_exclude_fixes_without_hiding_findings(
    copy: str, original: str, escaped: str
) -> None:
    source = (
        "import os.path as osp\nimport pathlib as paths\n"
        + copy.replace("original", original)
        + "\nresult = osp.basename('a/b')\n"
    )
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert escaped in detector.escaped_origins
    assert len(source.splitlines()) in detector.offenses
    assert detector.resolve(ast.parse("osp.basename", mode="eval").body) == "os.path.basename"
    assert fix_content(source) == (source, 0)


def test_alias_copy_cycles_have_finite_mutation_origins(source_env: dict[str, str]) -> None:
    source = (
        "import os as alias\nalias = alias.path\nalias = alias.basename\n"
        "second = alias\nalias = second.child\n"
        "alias.__code__ = custom.__code__\nimport os.path as osp\n"
    )
    program = (
        "import ast, sys\nfrom pathlint.linter import OSPathDetector\n"
        "source = sys.argv[1]\ndetector = OSPathDetector()\n"
        "detector.visit(ast.parse(source))\n"
        "assert 'os.path.basename.__code__' in detector.mutated_attributes\n"
        "assert detector.resolve(ast.parse('osp.basename', mode='eval').body) is None\n"
    )
    subprocess.run([sys.executable, "-c", program, source], env=source_env, timeout=5, check=True)


def test_copied_alias_calls_remain_unresolved() -> None:
    source = "import os.path as osp\nalias = osp\nresult = alias.basename('a/b')\n"
    assert offenses(source) == {1, 2}
    assert fix_content(source) == (source, 0)


@pytest.mark.parametrize(
    ("import_statement", "attribute"),
    [
        ("import os.path as alias", "alias.basename"),
        ("from os import path as alias", "alias.basename"),
        ("import os as alias", "alias.path.basename"),
    ],
)
@pytest.mark.parametrize("shadow", ["alias = object()", "import math as alias"])
@pytest.mark.parametrize("condition", ["False", "flag"])
def test_uncertain_alias_mutations_retain_possible_import_origin(
    import_statement: str, attribute: str, shadow: str, condition: str
) -> None:
    source = (
        f"{import_statement}\n"
        f"if {condition}:\n"
        f"    {shadow}\n"
        f"{attribute} = custom\n"
        "import os.path as other\n"
    )
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {"os.path.basename"}
    assert detector.resolve(ast.parse("other.basename", mode="eval").body) is None
    assert detector.resolve(ast.parse("other.dirname", mode="eval").body) == "os.path.dirname"
    # Unknown branches retain possible mutations without establishing read bindings.
    expected_binding = None
    if condition == "False":
        expected_binding = "os" if import_statement == "import os as alias" else "os.path"
    assert detector.resolve(ast.parse("alias", mode="eval").body) == expected_binding


def test_function_resolves_import_defined_after_definition() -> None:
    source = """
        def f():
            return os.path.exists('file')
        import os
        result = f()
    """
    assert offenses(source) == {2}


@pytest.mark.parametrize("declaration", ["global", "nonlocal"])
@pytest.mark.parametrize("reader_first", [False, True])
@pytest.mark.parametrize("late_import", [False, True])
@pytest.mark.parametrize("reader_kind", ["function", "lambda", "generator"])
def test_uncalled_deferred_writer_does_not_change_sibling_bindings(
    declaration: str, reader_first: bool, late_import: bool, reader_kind: str
) -> None:
    writer = (
        "def uncalled():\n"
        "    class ChangesBinding:\n"
        f"        {declaration} os\n"
        "        os = object()\n"
    )
    call = "os.path.basename('a/b')"
    if reader_kind == "function":
        reader = f"def reader():\n    return {call}\n"
        result = "reader()"
    elif reader_kind == "lambda":
        reader = f"reader = lambda: {call}\n"
        result = "reader()"
    else:
        reader = f"reader = ({call} for _ in [0])\n"
        result = "next(reader)"
    body = reader + writer if reader_first else writer + reader
    body = body + "import os\n" if late_import else "import os\n" + body
    source = (
        "def outer():\n" + indent(body + f"return {result}\n", "    ") + "result = outer()\n"
        if declaration == "nonlocal"
        else body + f"result = {result}\n"
    )
    target = next(index for index, line in enumerate(source.splitlines(), 1) if call in line)
    detector = OSPathDetector()
    detector.visit(ast.parse(source))
    assert detector.offenses == {target}
    if declaration == "global":
        assert detector.resolve_name("os") == "os"
    assert execute(source)["result"] == "b"


@pytest.mark.parametrize(
    "source",
    [
        "import os\nif False:\n    os = object()\nresult = os.path.isabs('missing')\n",
        "try:\n    import os\nexcept ImportError:\n    pass\nresult = os.path.isabs('missing')\n",
        "from contextlib import nullcontext\nwith nullcontext():\n    import os\n"
        "result = os.path.isabs('missing')\n",
        pytest.param(
            "match 1:\n    case 1:\n        import os\nresult = os.path.isabs('missing')\n",
            marks=pytest.mark.skipif(not hasattr(ast, "Match"), reason="requires Python 3.10"),
        ),
        pytest.param(
            "try:\n    import os\nexcept* ImportError:\n    pass\n"
            "result = os.path.isabs('missing')\n",
            marks=pytest.mark.skipif(not hasattr(ast, "TryStar"), reason="requires Python 3.11"),
        ),
        "import asyncio\nfrom contextlib import asynccontextmanager\n"
        "@asynccontextmanager\nasync def context():\n    yield\n"
        "async def checked():\n    async with context():\n        import os\n"
        "    return os.path.isabs('missing')\nresult = asyncio.run(checked())\n",
    ],
    ids=["literal-if", "try", "with", "match", "try-star", "async-with"],
)
def test_detection_reports_possible_imports_but_fixing_requires_definite_bindings(
    source: str,
) -> None:
    target = next(
        index for index, line in enumerate(source.splitlines(), 1) if "os.path.isabs" in line
    )
    assert offenses(source) == {target}
    assert fix_content(source) == (source, 0)
    assert execute(source)["result"] is False


def test_function_respects_global_reassignment_before_call() -> None:
    source = """
        import os
        from types import SimpleNamespace
        def f():
            return os.path.exists('file')
        os = SimpleNamespace(path=SimpleNamespace(exists=lambda value: False))
        result = f()
    """
    assert not offenses(source)


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
    assert not offenses(source)
