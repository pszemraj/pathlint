"""Preserve source headers, namespace references, and imports while editing."""

from __future__ import annotations

import ast

from pathlint._source import _Source, is_docstring, physical_lines
from pathlint.bindings import _ScopeNames, import_binding

_NAMESPACE_BUILTINS = frozenset(
    {
        "globals",
        "locals",
        "vars",
        "eval",
        "exec",
        "__dict__",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
    }
)
_ATTRIBUTE_MUTATORS = frozenset({"__setattr__", "__delattr__"})
_NAMESPACE_IMPORTS = {
    "builtins": _NAMESPACE_BUILTINS,
    "sys": frozenset({"modules"}),
    "importlib": frozenset({"import_module"}),
}
_NAMESPACE_ATTRIBUTES = _ATTRIBUTE_MUTATORS.union(*_NAMESPACE_IMPORTS.values()) | {
    "__globals__",
    "__builtins__",
    "__getattribute__",
    "__getattr__",
    "__closure__",
    "__defaults__",
    "__kwdefaults__",
    "__annotations__",
    "cell_contents",
    "f_globals",
    "f_locals",
    "f_builtins",
}


def _insert_import(content: str, name: str) -> str:
    """Insert a Path import after the module header while preserving newline style.

    :param str content: Python source to receive the import.
    :param str name: Private local alias for the inserted constructor.
    :return str: Source with the import before executable statements and after headers.
    """
    tree = ast.parse(content)
    lines = physical_lines(content)
    newline = "\r\n" if "\r\n" in content else "\r" if "\r" in content else "\n"
    statement = f"from pathlib import Path as {name}"
    insertion = tree.body[0].lineno - 1 if tree.body else len(lines)
    if tree.body and getattr(tree.body[0], "decorator_list", []):
        # Decorator expressions can start below their opening @(... line.
        insertion = next(index for index, line in enumerate(lines) if line.lstrip().startswith("@"))
    for index, node in enumerate(tree.body):
        if (index == 0 and is_docstring(node)) or isinstance(node, (ast.Import, ast.ImportFrom)):
            insertion = node.end_lineno
        else:
            if node.lineno <= insertion:
                # A leading import shares its physical line with executable code.
                # Insert before ordinary imports, but after docstrings/future imports.
                header = None
                for header_index, candidate in enumerate(tree.body):
                    if (header_index == 0 and is_docstring(candidate)) or (
                        isinstance(candidate, ast.ImportFrom) and candidate.module == "__future__"
                    ):
                        header = candidate
                    else:
                        break
                source = _Source(content)
                if header is None:
                    offset = source.starts[tree.body[0].lineno - 1]
                else:
                    _, offset = source.span(header)
                    while offset < len(content) and content[offset] in " \t":
                        offset += 1
                    if content[offset : offset + 1] == ";":
                        offset += 1
                        while offset < len(content) and content[offset] in " \t":
                            offset += 1
                    else:
                        offset = source.starts[header.end_lineno]
                prefix = content[:offset].rstrip("; \t")
                if prefix and not prefix.endswith(("\r", "\n")):
                    prefix += newline
                return prefix + statement + newline + content[offset:]
            break
    prefix = "".join(lines[:insertion])
    if prefix and not prefix.endswith(("\r", "\n")):
        prefix += newline
    return prefix + statement + newline + "".join(lines[insertion:])


def _references_namespace(nodes: tuple[ast.AST, ...], *, include_dir: bool = False) -> bool:
    """Find references exposing runtime namespaces or dynamic code execution.

    :param tuple[ast.AST, ...] nodes: Parsed source nodes, including nested scopes.
    :param bool include_dir: Include read-only name listings for import retention.
    :return bool: Whether a known namespace capability is referenced.
    """
    namespace_names = (
        set(_NAMESPACE_BUILTINS)
        | {"__builtins__", "__annotations__"}
        | {
            alias.asname or alias.name
            for node in nodes
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name in _ATTRIBUTE_MUTATORS
            or (
                not node.level
                and (
                    alias.name in _NAMESPACE_IMPORTS.get(node.module, ())
                    or (include_dir and node.module == "builtins" and alias.name == "dir")
                )
            )
        }
    )
    namespace_names.update(
        alias.asname or alias.name
        for node in nodes
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "builtins"
    )
    if include_dir:
        namespace_names.add("dir")
    return any(
        isinstance(getattr(node, "ctx", None), ast.Load)
        and (
            (isinstance(node, ast.Name) and node.id in namespace_names)
            or (
                isinstance(node, ast.Attribute)
                and (node.attr in _NAMESPACE_ATTRIBUTES or (include_dir and node.attr == "dir"))
            )
        )
        for node in nodes
    )


def _clean_imports(content: str) -> tuple[str, int]:
    """Remove unused os.path import aliases without discarding import comments.

    :param str content: Python source after call transformations.
    :return tuple[str, int]: Updated source and the count of aliases removed or simplified.
    """
    tree = ast.parse(content)
    nodes = tuple(ast.walk(tree))
    source = _Source(content)
    if any(
        (isinstance(node, ast.Name) and node.id == "__all__")
        or (isinstance(node, ast.alias) and (node.asname or node.name) == "__all__")
        for node in nodes
    ):
        # Explicit exports can refer to imports by string, including imported lists.
        return content, 0
    if any(
        getattr(node, "annotation", None) is not None
        or getattr(node, "returns", None) is not None
        or (isinstance(node, ast.Name) and node.id == "__annotations__")
        or (isinstance(node, ast.Attribute) and node.attr == "__annotations__")
        for node in nodes
    ):
        # Annotation aliases can resolve to strings that need imports after loading.
        return content, 0
    if _references_namespace(nodes, include_dir=True):
        # Namespace inspectors and dynamic code can access arbitrary imported bindings.
        return content, 0
    # A later del still needs its imported binding after calls are rewritten.
    referenced = {
        node.id
        for node in nodes
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Load, ast.Del))
    }
    # Module imports are externally accessible even without explicit __all__.
    module_names = _ScopeNames()
    for statement in tree.body:
        module_names.visit(statement)
    referenced.update(module_names.imports)
    # Augmented assignment reads its target even though the AST marks it as Store.
    referenced.update(
        node.target.id
        for node in nodes
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
    )
    # Global imports publish module bindings; nonlocal imports require their owner.
    referenced.update(
        name
        for node in nodes
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    )
    for node in nodes:
        if isinstance(node, ast.ClassDef):
            # Class imports expose attributes even without a bare-name use.
            bindings = _ScopeNames()
            for statement in node.body:
                bindings.visit(statement)
            referenced.update(bindings.names)
    os_path_used = any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr == "path"
        for node in nodes
    )
    count = 0
    for node in nodes:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        node_count = 0
        remaining = []
        for alias in node.names:
            name, _ = import_binding(node, alias)
            if isinstance(node, ast.Import):
                if (
                    alias.name == "os.path"
                    and not alias.asname
                    and name in referenced
                    and not os_path_used
                ):
                    remaining.append(ast.alias(name="os", asname=None))
                    node_count += 1
                    continue
                removable = alias.name == "os.path"
            else:
                removable = (
                    alias.name != "*"
                    and not node.level
                    and (node.module == "os.path" or (node.module == "os" and alias.name == "path"))
                )
            if removable and name not in referenced:
                node_count += 1
            else:
                remaining.append(alias)
        if remaining == node.names:
            continue
        start, end = source.span(node)
        # Keep comments inside multiline import statements in their original positions.
        if "#" in content[start:end]:
            continue
        if remaining:
            if isinstance(node, ast.Import):
                replacement = "import "
            else:
                replacement = f"from {'.' * node.level}{node.module or ''} import "
            replacement += ", ".join(
                alias.name + (f" as {alias.asname}" if alias.asname else "") for alias in remaining
            )
        else:
            # Public imports are retained above; local suites still need a statement.
            replacement = "pass"
        source.edits.append((start, end, replacement))
        count += node_count
    return source.render(), count
