"""Preserve source headers, namespace references, and imports while editing."""

from __future__ import annotations

import ast

from pathlint._source import _Source, is_docstring, physical_lines
from pathlint.bindings import _ScopeNames


def _skip_semicolon(
    content: str, offset: int, following: str, *, whitespace: str | None = " \t"
) -> int | None:
    """Locate text after a following semicolon and its horizontal whitespace.

    :param str content: Original source used to skip whitespace after the separator.
    :param int offset: Start offset of the following text.
    :param str following: Source interval in which to look for the separator.
    :param str | None whitespace: Characters allowed before it, or all whitespace.
    :return int | None: Offset after the separator, or None if it is absent.
    """
    stripped = following.lstrip(whitespace)
    if not stripped.startswith(";"):
        return None
    offset += len(following) - len(stripped) + 1
    while offset < len(content) and content[offset] in " \t":
        offset += 1
    return offset


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
                    after_separator = _skip_semicolon(content, offset, content[offset:])
                    offset = (
                        after_separator
                        if after_separator is not None
                        else source.starts[header.end_lineno]
                    )
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
    builtins = {
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
    if include_dir:
        builtins.add("dir")
    attribute_mutators = {"__setattr__", "__delattr__"}
    namespace_imports = {
        "builtins": builtins,
        "sys": {"modules"},
        "importlib": {"import_module"},
    }
    namespace_names = (
        builtins
        | {"__builtins__", "__annotations__"}
        | {
            alias.asname or alias.name
            for node in nodes
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name in attribute_mutators
            or (not node.level and alias.name in namespace_imports.get(node.module, ()))
        }
    )
    namespace_names.update(
        alias.asname or alias.name
        for node in nodes
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "builtins"
    )
    namespace_attributes = attribute_mutators.union(*namespace_imports.values()) | {
        "__globals__",
        "__dict__",
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
    return any(
        isinstance(getattr(node, "ctx", None), ast.Load)
        and (
            (isinstance(node, ast.Name) and node.id in namespace_names)
            or (isinstance(node, ast.Attribute) and node.attr in namespace_attributes)
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
            name = alias.asname or (
                alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name
            )
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
            replacement = ""
            line_stop = source.starts[node.end_lineno]
            line_end = line_stop
            while line_end > end and content[line_end - 1] in "\r\n":
                line_end -= 1
            following = content[end:line_end]
            after_separator = _skip_semicolon(content, end, following, whitespace=None)
            if after_separator is not None:
                end = after_separator
            else:
                line_start = source.starts[node.lineno - 1]
                preceding = content[line_start:start]
                if preceding.rstrip().endswith(";"):
                    start = line_start + len(preceding.rstrip()) - 1
                elif content[line_start:start].strip() == "" and following.strip() == "":
                    start = line_start
                    end = line_stop
                else:
                    # An empty suite still needs a statement.
                    replacement = "pass"
            # Keep local suites valid and ordinary strings from becoming docstrings.
            if node.col_offset and replacement == "":
                replacement = "pass"
                start, end = source.span(node)
        source.replace(start, end, replacement)
        count += node_count
    return source.render(0, len(content)), count
