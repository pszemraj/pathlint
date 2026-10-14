#!/usr/bin/env python3
"""Detect os.path imports, aliases, calls, and annotations."""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path


class _ScopeNames(ast.NodeVisitor):
    """Collect local bindings without descending into nested scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.external: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_arg(self, node: ast.arg) -> None:
        self.names.add(node.arg)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default:
                self.visit(default)

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        for generator in node.generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_Global(self, node: ast.Global) -> None:
        self.external.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.AST) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node: ast.AST) -> None:
        if node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)


class OSPathDetector(ast.NodeVisitor):
    """Find os.path usage while respecting import aliases and local shadowing."""

    def __init__(self, lines: list[str]) -> None:
        self.offenses: dict[int, set[str]] = defaultdict(set)
        self.lines = lines
        self.scopes: list[dict[str, str | None]] = [{}]
        self.scope_kinds = ["module"]
        self.os_imported = False
        self.path_aliases: set[str] = set()
        self.deferred: list[
            tuple[
                ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
                list[dict[str, str | None]],
                list[str],
            ]
        ] = []

    def visit_Module(self, node: ast.Module) -> None:
        for statement in node.body:
            self.visit(statement)
        # Function bodies use enclosing bindings after those scopes finish defining names.
        # This also avoids rewriting aliases that the enclosing scope reassigns later.
        for function, scopes, kinds in self.deferred:
            self.scopes, self.scope_kinds = scopes.copy(), kinds.copy()
            names = _ScopeNames()
            names.visit(function.args)
            body = [function.body] if isinstance(function, ast.Lambda) else function.body
            for statement in body:
                names.visit(statement)
            self.scopes.append(dict.fromkeys(names.names - names.external))
            self.scope_kinds.append("function")
            for statement in body:
                self.visit(statement)
        self.scopes, self.scope_kinds = [self.scopes[0]], ["module"]

    def resolve_name(self, name: str) -> str | None:
        """Resolve an imported name in the current lexical scope."""
        for index in range(len(self.scopes) - 1, -1, -1):
            if self.scope_kinds[index] == "class" and index != len(self.scopes) - 1:
                continue
            if name in self.scopes[index]:
                return self.scopes[index][name]
        return None

    def resolve(self, node: ast.AST) -> str | None:
        """Resolve an imported name or attribute to its original spelling."""
        if isinstance(node, ast.Name):
            return self.resolve_name(node.id)
        if isinstance(node, ast.Attribute):
            parent = self.resolve(node.value)
            return f"{parent}.{node.attr}" if parent else None
        return None

    def _record(self, node: ast.AST) -> None:
        if hasattr(node, "lineno") and 0 < node.lineno <= len(self.lines):
            self.offenses[node.lineno].add(self.lines[node.lineno - 1].strip())

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            self.scopes[-1][name] = alias.name if alias.asname else alias.name.split(".")[0]
            if alias.name == "os":
                self.os_imported = True
            elif alias.name == "os.path" or alias.name.startswith("os.path."):
                self._record(node)
                self.path_aliases.add(alias.asname or "os.path")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            name = alias.asname or alias.name
            origin = f"{node.module}.{alias.name}" if node.module and not node.level else None
            self.scopes[-1][name] = origin
            if origin and (origin == "os.path" or origin.startswith("os.path.")):
                self._record(node)
                self.path_aliases.add(name)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            origin = self.resolve(node)
            if origin and (origin == "os.path" or origin.startswith("os.path.")):
                self._record(node)
        else:
            self.scopes[-1][node.id] = None

    def visit_Attribute(self, node: ast.Attribute) -> None:
        origin = self.resolve(node)
        if (
            isinstance(node.ctx, ast.Load)
            and origin
            and (origin == "os.path" or origin.startswith("os.path."))
        ):
            self._record(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns:
            self.visit(node.returns)
        self.scopes[-1][node.name] = None
        self.deferred.append((node, self.scopes.copy(), self.scope_kinds.copy()))

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self.deferred.append((node, self.scopes.copy(), self.scope_kinds.copy()))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in (*node.decorator_list, *node.bases, *node.keywords):
            self.visit(expression)
        self.scopes[-1][node.name] = None
        self.scopes.append({})
        self.scope_kinds.append("class")
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()
        self.scope_kinds.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value:
            self.visit(node.value)
        self.visit(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self.visit(node.target)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self.visit(node.target)
        for statement in (*node.body, *node.orelse):
            self.visit(statement)

    visit_AsyncFor = visit_For

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self.visit(node.target)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type:
            self.visit(node.type)
        if node.name:
            self.scopes[-1][node.name] = None
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.AST) -> None:
        if node.name:
            self.scopes[-1][node.name] = None
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node: ast.AST) -> None:
        if node.rest:
            self.scopes[-1][node.rest] = None
        self.generic_visit(node)

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        self.visit(node.generators[0].iter)
        self.scopes.append({})
        self.scope_kinds.append("comprehension")
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.scopes.pop()
        self.scope_kinds.pop()

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


def _lint_file(filepath: Path) -> list[tuple[int, str]]:
    content = filepath.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(filepath))
    if "os" not in content and "path" not in content:
        return []
    detector = OSPathDetector(content.splitlines())
    detector.visit(tree)
    return [(line, next(iter(sources))) for line, sources in sorted(detector.offenses.items())]


def lint_file(filepath: Path) -> list[tuple[int, str]]:
    """Lint a file, preserving the original list-returning helper interface."""
    try:
        return _lint_file(filepath)
    except SyntaxError as error:
        print(f"✗ Syntax error in {filepath}:{error.lineno}:{error.offset}", file=sys.stderr)
    except (OSError, UnicodeError) as error:
        print(f"✗ Cannot read {filepath}: {error.__class__.__name__}", file=sys.stderr)
    return []


def find_python_files(paths: list[str], exclude_patterns: set[str] | None = None) -> set[Path]:
    """Collect Python files, retaining the legacy custom directory exclusions."""
    from pathlint.discovery import DEFAULT_EXCLUDES, discover_files

    files, errors = discover_files(
        [Path(path) for path in paths],
        default_excludes=DEFAULT_EXCLUDES if exclude_patterns is None else exclude_patterns,
    )
    for path, message in errors:
        print(f"{path}: error: {message}", file=sys.stderr)
    return set(files)


def main(argv: list[str] | None = None) -> int:
    """Forward the original entry point to the shared CLI."""
    from pathlint.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
