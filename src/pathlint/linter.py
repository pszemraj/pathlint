#!/usr/bin/env python3
"""Detect os.path imports, aliases, calls, and annotations."""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from io import StringIO
from pathlib import Path


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

    def visit_Import(self, node: ast.Import) -> None:
        """Collect the names introduced by an import statement."""
        names = {alias.asname or alias.name.split(".")[0] for alias in node.names}
        self.names.update(names)
        self.imports.update(names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Collect the aliases introduced by a from-import statement."""
        names = {alias.asname or alias.name for alias in node.names}
        self.names.update(names)
        self.imports.update(names)

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
        """Collect a named match capture and visit its nested pattern."""
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node: ast.AST) -> None:
        """Collect a mapping-rest capture and visit its nested patterns."""
        if node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)


class OSPathDetector(ast.NodeVisitor):
    """Find os.path usage while respecting import aliases and local shadowing."""

    def __init__(self, lines: list[str]) -> None:
        """Initialize lexical bindings and offenses for the supplied source lines."""
        self.offenses: dict[int, set[str]] = defaultdict(set)
        self.lines = lines
        self.scopes: list[dict[str, str | None]] = [{}]
        self.scope_kinds = ["module"]
        self.scope_globals: list[set[str]] = [set()]
        self.os_imported = False
        self.path_aliases: set[str] = set()
        self.mutated_attributes: set[str] = set()
        self.escaped_origins: set[str] = set()
        self.import_origins: dict[str, set[str]] = defaultdict(set)
        self.mutation_aliases: dict[str, set[str]] = defaultdict(set)
        self.deferred: list[
            tuple[
                ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
                list[dict[str, str | None]],
                list[str],
                list[set[str]],
            ]
        ] = []

    def visit_Module(self, node: ast.Module) -> None:
        """Visit module statements, then deferred bodies using final enclosing bindings."""
        self._collect_mutation_aliases(node)
        for statement in node.body:
            self.visit(statement)
        # Function bodies use enclosing bindings after those scopes finish defining names.
        # This also avoids rewriting aliases that the enclosing scope reassigns later.
        for function, scopes, kinds, globals_ in self.deferred:
            self.scopes, self.scope_kinds = scopes.copy(), kinds.copy()
            self.scope_globals = globals_.copy()
            names = _ScopeNames()
            names.names.update(
                argument.arg
                for argument in (
                    *function.args.posonlyargs,
                    *function.args.args,
                    *function.args.kwonlyargs,
                    function.args.vararg,
                    function.args.kwarg,
                )
                if argument
            )
            body = [function.body] if isinstance(function, ast.Lambda) else function.body
            for statement in body:
                names.visit(statement)
            self.scopes.append(dict.fromkeys(names.names - names.external))
            self.scope_kinds.append("function")
            self.scope_globals.append(names.globals)
            for statement in body:
                self.visit(statement)
        self.scopes, self.scope_kinds = [self.scopes[0]], ["module"]
        self.scope_globals = [set()]

    def resolve_name(self, name: str) -> str | None:
        """Resolve an imported name in the current lexical scope.

        :return str | None: Import spelling, or None for an unbound or shadowed name.
        """
        if name.startswith("__") and not name.endswith("__") and "class" in self.scope_kinds:
            # Private identifiers use a mangled spelling throughout class scopes.
            return None
        for index in range(len(self.scopes) - 1, -1, -1):
            if self.scope_kinds[index] == "class" and index != len(self.scopes) - 1:
                continue
            if name in self.scopes[index]:
                return self.scopes[index][name]
            if name in self.scope_globals[index]:
                return self.scopes[0].get(name)
        return None

    def resolve(self, node: ast.AST) -> str | None:
        """Resolve an imported name or attribute to its original spelling.

        :return str | None: Qualified import spelling, or None if unresolved or mutated.
        """
        origin = None
        if isinstance(node, ast.Name):
            origin = self.resolve_name(node.id)
        elif isinstance(node, ast.Attribute):
            parent = self.resolve(node.value)
            origin = f"{parent}.{node.attr}" if parent else None
        # Callable internals affect calls; module parents must retain sibling methods.
        if origin and any(
            origin == changed
            or origin.startswith(changed + ".")
            or (origin.startswith(("os.path.", "pathlib.")) and changed.startswith(origin + "."))
            for changed in self.mutated_attributes
        ):
            return None
        return origin

    def _record(self, node: ast.AST) -> None:
        """Record the stripped source line for a node with a valid line number."""
        if hasattr(node, "lineno") and 0 < node.lineno <= len(self.lines):
            self.offenses[node.lineno].add(self.lines[node.lineno - 1].strip())

    def _mutation_origins(self, node: ast.AST) -> set[str]:
        """Retain possible import origins for writes without reviving read bindings.

        :param ast.AST node: Attribute target or its base expression.
        :return set[str]: Previously imported spellings that the target may mutate.
        """
        if isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load):
                return set()
            return self.import_origins.get(node.id, set()) | self.mutation_aliases.get(
                node.id, set()
            )
        if isinstance(node, ast.Attribute):
            return {f"{origin}.{node.attr}" for origin in self._mutation_origins(node.value)}
        if isinstance(node, ast.Call):
            # Calls may return their inputs; invoking an imported callable does
            # not itself copy that callable. Return types are not inferred.
            return set().union(
                *(self._mutation_origins(value) for value in [*node.args, *node.keywords])
            )
        # Expressions can retain or return their referenced objects. Do not use
        # their syntax to prove that an imported namespace cannot be copied.
        return set().union(*(self._mutation_origins(child) for child in ast.iter_child_nodes(node)))

    def _collect_mutation_aliases(self, node: ast.Module) -> None:
        """Collect possible copies before visiting deferred writers, without binding reads.

        :param ast.Module node: Module containing imports and assignment copies.
        """
        copies: list[tuple[ast.AST | None, ast.AST]] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    name = alias.asname or alias.name.split(".")[0]
                    origin = alias.name if alias.asname else alias.name.split(".")[0]
                    self.mutation_aliases[name].add(origin)
            elif isinstance(child, ast.ImportFrom) and child.module and not child.level:
                for alias in child.names:
                    self.mutation_aliases[alias.asname or alias.name].add(
                        f"{child.module}.{alias.name}"
                    )
            elif isinstance(child, ast.Assign):
                copies.extend((target, child.value) for target in child.targets)
            elif (
                isinstance(child, (ast.AnnAssign, ast.NamedExpr, ast.AugAssign))
                and child.value is not None
            ):
                copies.append((child.target, child.value))
            elif isinstance(child, (ast.For, ast.AsyncFor, ast.comprehension)):
                copies.append((child.target, child.iter))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if isinstance(child, ast.Lambda):
                    copies.append((None, child.body))
                positional = [*child.args.posonlyargs, *child.args.args]
                copies.extend(
                    zip(
                        positional[len(positional) - len(child.args.defaults) :],
                        child.args.defaults,
                    )
                )
                copies.extend(
                    (argument, default)
                    for argument, default in zip(child.args.kwonlyargs, child.args.kw_defaults)
                    if default is not None
                )
            elif isinstance(child, ast.Call):
                copies.extend((None, value) for value in [*child.args, *child.keywords])
            elif (
                isinstance(child, (ast.Return, ast.Yield, ast.YieldFrom))
                and child.value is not None
            ):
                copies.append((None, child.value))
            elif isinstance(child, ast.withitem):
                copies.append((None, child.context_expr))
            elif isinstance(child, getattr(ast, "Match", ())):
                copies.append((None, child.subject))
            elif isinstance(child, ast.ClassDef):
                names = _ScopeNames()
                for statement in child.body:
                    names.visit(statement)
                copies.append((None, child))
                copies.extend((None, ast.Name(id=name, ctx=ast.Load())) for name in names.imports)
        # Copies can cross deferred bodies in either declaration order. Retaining
        # every possible origin deliberately sacrifices scope/order precision.
        changed = True
        while changed:
            changed = False
            for target, value in copies:
                changed |= self._retain_mutation_alias(target, value)

    def _retain_mutation_alias(self, target: ast.AST | None, value: ast.AST) -> bool:
        """Retain bounded import origins for a direct or unpacked assignment copy.

        :param ast.AST | None target: Assignment target or None when the copied value escapes.
        :param ast.AST value: Expression copied into the target.
        :return bool: Whether any possible mutation origin was added.
        """
        if isinstance(target, ast.Starred):
            return self._retain_mutation_alias(target.value, value)
        if target is None or isinstance(target, (ast.Name, ast.arg, ast.Attribute, ast.Subscript)):
            origins = set()
            for origin in self._mutation_origins(value):
                if origin == "os":
                    origins.add(origin)
                elif origin == "os.path" or origin.startswith("os.path."):
                    origins.add(".".join(origin.split(".")[:3]))
                elif origin == "pathlib" or origin.startswith("pathlib."):
                    origins.add(".".join(origin.split(".")[:2]))
            # Collapse copied callable internals to their exported parent. This
            # overapproximates mutations and prevents cyclic copies growing paths.
            if target is None or isinstance(target, (ast.Attribute, ast.Subscript)):
                # Escaping objects can be written through unmodeled aliases.
                # Exclude fixes without changing the detector's read bindings.
                before = len(self.escaped_origins)
                self.escaped_origins.update(
                    "os.path" if origin == "os" else origin for origin in origins
                )
                return len(self.escaped_origins) != before
            name = target.id if isinstance(target, ast.Name) else target.arg
            before = len(self.mutation_aliases[name])
            self.mutation_aliases[name].update(origins)
            return len(self.mutation_aliases[name]) != before
        changed = False
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            if len(target.elts) == len(value.elts) and not any(
                isinstance(item, ast.Starred) for item in (*target.elts, *value.elts)
            ):
                for item, expression in zip(target.elts, value.elts):
                    changed |= self._retain_mutation_alias(item, expression)
            else:
                # Starred unpacking can shift positions. Every literal element
                # is a possible origin for each remaining assignment target.
                for item in target.elts:
                    for expression in value.elts:
                        changed |= self._retain_mutation_alias(item, expression)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                changed |= self._retain_mutation_alias(item, value)
        return changed

    def visit_Import(self, node: ast.Import) -> None:
        """Bind imported modules and record direct os.path imports."""
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            origin = alias.name if alias.asname else alias.name.split(".")[0]
            self.scopes[-1][name] = origin
            self.import_origins[name].add(origin)
            if alias.name == "os":
                self.os_imported = True
            elif alias.name == "os.path" or alias.name.startswith("os.path."):
                self._record(node)
                self.path_aliases.add(alias.asname or "os.path")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Bind from-import aliases and record absolute os.path imports."""
        for alias in node.names:
            name = alias.asname or alias.name
            origin = f"{node.module}.{alias.name}" if node.module and not node.level else None
            self.scopes[-1][name] = origin
            if origin:
                self.import_origins[name].add(origin)
            if origin and (origin == "os.path" or origin.startswith("os.path.")):
                self._record(node)
                self.path_aliases.add(name)

    def visit_Name(self, node: ast.Name) -> None:
        """Record loaded os.path aliases or shadow names assigned or deleted."""
        if isinstance(node.ctx, ast.Load):
            origin = self.resolve(node)
            if origin and (origin == "os.path" or origin.startswith("os.path.")):
                self._record(node)
        elif isinstance(node.ctx, ast.Del):
            self._delete_name(node.id)
        else:
            self.scopes[-1][node.id] = None

    def _delete_name(self, name: str) -> None:
        """Remove a class local or retain an unbound name in other scopes.

        :param str name: Binding explicitly deleted or cleared after an exception handler.
        """
        if self.scope_kinds[-1] == "class":
            self.scopes[-1].pop(name, None)
        else:
            self.scopes[-1][name] = None

    def visit_If(self, node: ast.If) -> None:
        """Inspect branches independently and retain certain resulting bindings.

        :param ast.If node: Conditional statement to inspect for bindings and usages.
        """
        self.visit(node.test)
        scope = self.scopes[-1]
        before = scope.copy()
        for statement in node.body:
            self.visit(statement)
        body = scope.copy()
        scope.clear()
        scope.update(before)
        for statement in node.orelse:
            self.visit(statement)
        alternative = scope.copy()
        if isinstance(node.test, ast.Constant):
            # Still inspect both suites, but literal conditions determine later bindings.
            self.merge_bindings([body if node.test.value else alternative])
        else:
            self.merge_bindings([body, alternative])

    def merge_bindings(self, alternatives: list[dict[str, str | None]]) -> None:
        """Retain an import origin only when every alternative agrees on it.

        :param list[dict[str, str | None]] alternatives: Possible current-scope bindings.
        """
        scope = self.scopes[-1]
        merged = {
            name: alternatives[0].get(name)
            if all(option.get(name) == alternatives[0].get(name) for option in alternatives)
            else None
            for name in set().union(*(option.keys() for option in alternatives))
        }
        scope.clear()
        scope.update(merged)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Record loaded os.path attributes and invalidate assigned or deleted origins."""
        origin = self.resolve(node)
        if (
            isinstance(node.ctx, ast.Load)
            and origin
            and (origin == "os.path" or origin.startswith("os.path."))
        ):
            self._record(node)
        self.generic_visit(node)
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            # Conditional imports can look certain in sequential traversal. Retain
            # earlier origins conservatively for mutations, never ordinary reads.
            self.mutated_attributes.update(
                possible
                for possible in self._mutation_origins(node)
                if possible == "os.path" or possible.startswith(("os.path.", "pathlib."))
            )

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Visit definition expressions and defer the body for lexical analysis."""
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns:
            self.visit(node.returns)
        self.scopes[-1][node.name] = None
        self._defer_body(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def _defer_body(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        """Queue a body with live enclosing bindings for subsequent analysis.

        :param ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda node: Deferred body owner.
        """
        self.deferred.append(
            (node, self.scopes.copy(), self.scope_kinds.copy(), self.scope_globals.copy())
        )

    def visit_arguments(self, node: ast.arguments) -> None:
        """Visit function defaults before their subsequently evaluated annotations."""
        for default in (*node.defaults, *node.kw_defaults):
            if default:
                self.visit(default)
        for argument in (
            *node.posonlyargs,
            *node.args,
            node.vararg,
            *node.kwonlyargs,
            node.kwarg,
        ):
            if argument and argument.annotation:
                self.visit(argument.annotation)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Visit lambda arguments and defer its body for lexical analysis."""
        self.visit(node.args)
        self._defer_body(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit class definition expressions and the body in its own scope."""
        for expression in (*node.decorator_list, *node.bases, *node.keywords):
            self.visit(expression)
        names = _ScopeNames()
        for statement in node.body:
            names.visit(statement)
        # Class suites execute immediately. External writes can occur on any branch;
        # invalidate the owner rather than reviving an import from an untaken branch.
        for name in names.names & names.external:
            if name in names.globals:
                self.scopes[0][name] = None
            else:
                for scope, kind in reversed(list(zip(self.scopes, self.scope_kinds))):
                    if kind == "function" and name in scope:
                        scope[name] = None
                        break
        self.scopes.append({})
        self.scope_kinds.append("class")
        # Missing class locals use LOAD_NAME's global fallback, skipping any
        # enclosing function bindings. Explicit nonlocals retain lexical lookup.
        self.scope_globals.append(names.globals | (names.names - names.external))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()
        self.scope_kinds.pop()
        self.scope_globals.pop()
        self.scopes[-1][node.name] = None

    def visit_Assign(self, node: ast.Assign) -> None:
        """Visit the assigned value before updating target bindings."""
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Visit the value and actual assignment before its annotation expression."""
        if node.value:
            self.visit(node.value)
            self.visit(node.target)
        elif isinstance(node.target, ast.Attribute):
            self.visit(node.target.value)
        elif isinstance(node.target, ast.Subscript):
            self.visit(node.target.value)
            self.visit(node.target.slice)
        self.visit(node.annotation)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        """Visit a named expression's value before updating its target binding."""
        self.visit(node.value)
        self.visit(node.target)

    def visit_Dict(self, node: ast.Dict) -> None:
        """Visit each dictionary key and value in evaluation order."""
        for key, value in zip(node.keys, node.values):
            if key is not None:
                self.visit(key)
            self.visit(value)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        """Visit the iterable, bind loop targets, and traverse both loop suites."""
        self.visit(node.iter)
        self.visit(node.target)
        for statement in (*node.body, *node.orelse):
            self.visit(statement)

    visit_AsyncFor = visit_For

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """Visit an augmented target's expressions before its value and name binding."""
        if isinstance(node.target, ast.Attribute):
            self.visit(node.target.value)
        elif isinstance(node.target, ast.Subscript):
            self.visit(node.target.value)
            self.visit(node.target.slice)
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self.visit(node.target)
        elif isinstance(node.target, ast.Attribute):
            self.mutated_attributes.update(
                possible
                for possible in self._mutation_origins(node.target)
                if possible == "os.path" or possible.startswith(("os.path.", "pathlib."))
            )

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Visit the exception type and suite, then clear the deleted exception alias."""
        if node.type:
            self.visit(node.type)
        if node.name:
            self.scopes[-1][node.name] = None
        for statement in node.body:
            self.visit(statement)
        if node.name:
            self._delete_name(node.name)

    def visit_MatchAs(self, node: ast.AST) -> None:
        """Shadow a named match capture and visit its nested pattern."""
        if node.name:
            self.scopes[-1][node.name] = None
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node: ast.AST) -> None:
        """Shadow a mapping-rest capture and visit its nested patterns."""
        if node.rest:
            self.scopes[-1][node.rest] = None
        self.generic_visit(node)

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        """Visit comprehension targets locally and walrus bindings in their owner scope."""
        self.visit(node.generators[0].iter)
        names = _ScopeNames()
        names.visit(node)
        owner = len(self.scopes) - 1
        while self.scope_kinds[owner] in {"comprehension", "generator"}:
            owner -= 1
        # Walrus targets escape comprehension scopes. Invalidate before inspecting
        # repeated iterations; generator consumption and empty iterables are unknown.
        for name in names.names:
            self.scopes[owner][name] = None
        self.scopes.append({})
        self.scope_kinds.append(
            "generator" if isinstance(node, ast.GeneratorExp) else "comprehension"
        )
        self.scope_globals.append(set())
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
        self.scope_globals.pop()

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


def _lint_file(filepath: Path) -> list[tuple[int, str]]:
    """Read and parse a Python file, propagating read and syntax errors.

    :return list[tuple[int, str]]: One (line, stripped source) per offending line, sorted.
    """
    content = filepath.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(filepath))
    if "os" not in content and "path" not in content:
        return []
    detector = OSPathDetector(list(StringIO(content, newline="")))
    detector.visit(tree)
    return [(line, next(iter(sources))) for line, sources in sorted(detector.offenses.items())]


def lint_file(filepath: Path) -> list[tuple[int, str]]:
    """Lint a file, reporting read and syntax errors to stderr.

    :return list[tuple[int, str]]: Sorted (line, stripped source) findings, or [] on error.
    """
    try:
        return _lint_file(filepath)
    except SyntaxError as error:
        print(f"✗ Syntax error in {filepath}:{error.lineno}:{error.offset}", file=sys.stderr)
    except (OSError, UnicodeError) as error:
        print(f"✗ Cannot read {filepath}: {error.__class__.__name__}", file=sys.stderr)
    return []


def find_python_files(paths: list[str], exclude_patterns: set[str] | None = None) -> set[Path]:
    """Collect Python files and report discovery errors to stderr.

    :param list[str] paths: File or directory inputs, using automatic Git ignores.
    :param set[str] | None exclude_patterns: Replacement exclusions; None uses defaults.
    :return set[Path]: Selected Python files, including any partial results.
    """
    from pathlint.discovery import DEFAULT_EXCLUDES, discover_files

    files, errors = discover_files(
        [Path(path) for path in paths],
        default_excludes=DEFAULT_EXCLUDES if exclude_patterns is None else exclude_patterns,
    )
    for path, message in errors:
        print(f"{path}: error: {message}", file=sys.stderr)
    return set(files)


def main(argv: list[str] | None = None) -> int:
    """Forward the original entry point to the shared CLI.

    :param list[str] | None argv: CLI arguments, or None to use process arguments.
    :return int: The shared CLI's exit status.
    """
    from pathlint.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
