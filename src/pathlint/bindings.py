"""Collect lexical scope bindings and declaration names."""

from __future__ import annotations

import ast


def declaration_name(node: ast.AST) -> str | None:
    """Return a definition, exception alias, or pattern capture name."""
    if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler)
    ) or type(node).__name__ in {"MatchAs", "MatchStar", "MatchMapping"}:
        return getattr(node, "name", None) or getattr(node, "rest", None)
    return None


class _ScopeNames(ast.NodeVisitor):
    """Collect local bindings without descending into nested scopes."""

    def __init__(self) -> None:
        """Initialize the sets of local and explicitly external names."""
        self.names: set[str] = set()
        self.external: set[str] = set()
        self.globals: set[str] = set()
        self.imports: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        """Collect names assigned or deleted in the current scope."""
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_arg(self, node: ast.arg) -> None:
        """Collect a function parameter as a local binding."""
        self.names.add(node.arg)

    def visit_Import(self, node: ast.Import | ast.ImportFrom) -> None:
        """Collect binding names introduced by either import syntax."""
        names = {
            alias.asname
            or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
            for alias in node.names
        }
        self.names.update(names)
        self.imports.update(names)

    visit_ImportFrom = visit_Import

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Collect function bindings and enclosing expressions without entering its body.

        :param ast.FunctionDef | ast.AsyncFunctionDef node: Function definition to inspect.
        """
        self.names.add(node.name)
        for expression in (
            *node.decorator_list,
            *node.args.defaults,
            *node.args.kw_defaults,
            node.returns,
        ):
            if expression:
                self.visit(expression)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            node.args.vararg,
            node.args.kwarg,
        ):
            if argument and argument.annotation:
                self.visit(argument.annotation)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Collect class bindings and enclosing expressions without entering its body.

        :param ast.ClassDef node: Class definition to inspect.
        """
        self.names.add(node.name)
        for expression in (*node.decorator_list, *node.bases, *node.keywords):
            self.visit(expression)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Visit lambda defaults without collecting its parameters or body."""
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default:
                self.visit(default)

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        """Visit comprehension expressions without collecting generator targets."""
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
        """Record names that must resolve against the module namespace."""
        self.external.update(node.names)
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        """Record names that resolve against an enclosing function namespace."""
        self.external.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Collect an exception alias and bindings within the handler."""
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.AST) -> None:
        """Collect named or mapping-rest captures and visit nested patterns."""
        name = declaration_name(node)
        if name:
            self.names.add(name)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs
    visit_MatchMapping = visit_MatchAs
