"""Regressions for import resolution and Python lexical scopes."""

from __future__ import annotations

import ast
import sys
from textwrap import dedent

import pytest

from pathlint.autofix import fix_content
from pathlint.linter import OSPathDetector


def offenses(source: str) -> dict[int, set[str]]:
    """Return offending lines for a small source snippet."""
    source = dedent(source).strip()
    detector = OSPathDetector(source.splitlines())
    detector.visit(ast.parse(source))
    return dict(detector.offenses)


@pytest.mark.parametrize(
    ("expression", "index", "expected", "changes"),
    [
        ("{'first': (os := custom), os.path.exists('missing'): 0}", -1, "custom", False),
        ("{**((os := custom) and {}), os.path.exists('missing'): 0}", 0, "custom", False),
        ("{os.path.exists('missing'): 0, 'last': (os := custom)}", 0, False, True),
    ],
)
def test_dictionary_entries_follow_runtime_evaluation_order(
    expression: str, index: int, expected: object, changes: bool
) -> None:
    source = (
        "import os\nfrom types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
        f"mapping = {expression}\nresult = list(mapping)[{index}]\n"
    )
    updated, count = fix_content(source)
    assert bool(count) is changes
    assert bool(offenses(source)) is changes
    for content in (source, updated):
        namespace: dict[str, object] = {}
        exec(compile(content, "<dictionary>", "exec", dont_inherit=True), namespace)
        assert namespace["result"] == expected


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


@pytest.mark.parametrize("declaration", ["global", "nonlocal"])
@pytest.mark.parametrize("module_import", [False, True])
def test_nested_external_declaration_selects_its_namespace(
    declaration: str, module_import: bool
) -> None:
    custom = "os = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))"
    module_binding = "import os" if module_import else custom
    outer_binding = custom if module_import else "import os"
    source = (
        "from types import SimpleNamespace\n"
        f"{module_binding}\n"
        "def outer():\n"
        f"    {outer_binding}\n"
        "    def inner():\n"
        f"        {declaration} os\n"
        "        return os.path.exists('unlikely-name')\n"
        "    return inner()\n"
        "result = outer()\n"
    )
    uses_import = module_import if declaration == "global" else not module_import
    assert set(offenses(source)) == ({7} if uses_import else set())
    updated, _ = fix_content(source)
    for content in (source, updated):
        namespace = {}
        exec(compile(content, "<external>", "exec", dont_inherit=True), namespace)
        assert namespace["result"] == (False if uses_import else "custom")


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
        "custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
    )
    body = (
        "os = custom\n"
        "class C:\n"
        f"    {declaration} os\n"
        + "\n".join("    " + line for line in statement.splitlines())
        + "\nresult = os.path.exists('unlikely-name')\n"
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
    assert offenses(source) == {}
    for content in (source, updated):
        namespace = {}
        exec(compile(content, "<class-external>", "exec", dont_inherit=True), namespace)
        assert namespace["result"] == "custom"


@pytest.mark.parametrize("declaration", ["global", "nonlocal"])
@pytest.mark.parametrize("statement", ["os = custom", "del os"])
def test_class_external_write_does_not_leave_import_visible(
    declaration: str, statement: str
) -> None:
    source = (
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
    )
    body = (
        "import os\n"
        "class C:\n"
        f"    {declaration} os\n"
        f"    {statement}\n"
        "    class Nested:\n"
        "        try:\n"
        "            result = os.path.exists('unlikely-name')\n"
        "        except NameError:\n"
        "            result = 'deleted'\n"
        "try:\n"
        "    later = os.path.exists('unlikely-name')\n"
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
    assert offenses(source) == {}
    for content in (source, updated):
        namespace = {}
        exec(compile(content, "<class-external>", "exec", dont_inherit=True), namespace)
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


def test_class_name_is_bound_after_its_body() -> None:
    source = "import os\nclass os:\n    result = os.path.exists('file')"
    assert set(offenses(source)) == {3}


@pytest.mark.parametrize("target", ["os", "os.path.exists", "os.path.__dict__['exists']"])
def test_annotation_only_target_does_not_assign(target: str) -> None:
    source = f"import os\n{target}: object\nos.path.exists('file')"
    assert 3 in offenses(source)


def test_annotation_only_function_name_remains_local() -> None:
    source = "import os\ndef f():\n    os: object\n    os.path.exists('file')"
    assert offenses(source) == {}


def test_annotated_assignment_binds_before_annotation() -> None:
    source = (
        "import os\n"
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
        "os: os.path.exists('file') = custom\n"
        "result = __annotations__['os']\n"
    )
    assert offenses(source) == {}
    updated, count = fix_content(source)
    assert count == 0
    for content in (source, updated):
        namespace = {}
        exec(compile(content, "<annotation>", "exec", dont_inherit=True), namespace)
        assert namespace["result"] == "custom"


@pytest.mark.parametrize("definition", ["def", "async def"])
@pytest.mark.parametrize("parameter", ["arg", "*, arg"])
def test_function_defaults_bind_before_annotations(definition: str, parameter: str) -> None:
    source = (
        "import os\n"
        "from types import SimpleNamespace\n"
        "custom = SimpleNamespace(path=SimpleNamespace(exists=lambda value: 'custom'))\n"
        f"{definition} f({parameter}: os.path.exists('unlikely-name') = (os := custom)):\n"
        "    pass\n"
        "result = f.__annotations__['arg']\n"
    )
    assert offenses(source) == {}
    updated, count = fix_content(source)
    assert count == 0
    for content in (source, updated):
        namespace = {}
        exec(compile(content, "<definition>", "exec", dont_inherit=True), namespace)
        assert namespace["result"] == "custom"


@pytest.mark.parametrize("parameter", ["arg=(os := os)", "arg: (os := os) = None"])
def test_definition_walrus_does_not_become_function_local(parameter: str) -> None:
    source = (
        "import os\n"
        f"def f({parameter}):\n"
        "    return os.path.exists('unlikely-name')\n"
        "import os\n"
        "result = f()\n"
    )
    assert set(offenses(source)) == {3}
    assert fix_content(source) == (source, 0)
    namespace = {}
    exec(compile(source, "<definition>", "exec", dont_inherit=True), namespace)
    assert namespace["result"] is False


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
    assert offenses(body) == {}


def test_comprehension_walrus_invalidates_calls_on_later_iterations() -> None:
    source = """
        import os as alias
        result = [(alias.path.exists('file'), (alias := custom)) for _ in [0, 1]]
    """
    assert offenses(source) == {}


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
    detector = OSPathDetector(source.splitlines())
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
    detector = OSPathDetector(source.splitlines())
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {"os.path"}
    for expression in ("osp", "osp.basename", "os.path.exists", "system.path.dirname"):
        assert detector.resolve(ast.parse(expression, mode="eval").body) is None


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
    detector = OSPathDetector(source.splitlines())
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {expected}
    assert (
        detector.resolve(
            ast.parse(statement.removeprefix("del ").split(" = ")[0], mode="eval").body
        )
        is None
    )
    if expected.startswith("pathlib.Path"):
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
    detector = OSPathDetector(source.splitlines())
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {"os.path.basename"}


@pytest.mark.parametrize(
    ("import_statement", "attribute"),
    [
        ("import os.path as alias", "alias.basename"),
        ("from os import path as alias", "alias.basename"),
        ("import os as alias", "alias.path.basename"),
    ],
)
@pytest.mark.parametrize("shadow", ["alias = object()", "import math as alias"])
def test_uncertain_alias_mutations_retain_possible_import_origin(
    import_statement: str, attribute: str, shadow: str
) -> None:
    source = (
        f"{import_statement}\n"
        "if False:\n"
        f"    {shadow}\n"
        f"{attribute} = custom\n"
        "import os.path as other\n"
    )
    detector = OSPathDetector(source.splitlines())
    detector.visit(ast.parse(source))
    assert detector.mutated_attributes == {"os.path.basename"}
    assert detector.resolve(ast.parse("other.basename", mode="eval").body) is None
    assert detector.resolve(ast.parse("other.dirname", mode="eval").body) == "os.path.dirname"
    # Historical imports establish possible mutation targets, not readable bindings.
    expected_binding = "math" if shadow.startswith("import") else None
    assert detector.resolve(ast.parse("alias", mode="eval").body) == expected_binding


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
