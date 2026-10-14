"""Detect os.path imports, aliases, calls, and annotations."""

from __future__ import annotations

import ast
from pathlib import Path

from pathlint._source import physical_lines
from pathlint.bindings import _ScopeNames, declaration_name
from pathlint.mutations import MutationCollector


class OSPathDetector(MutationCollector):
    """Find os.path usage while respecting import aliases and local shadowing."""

    _use_literal_conditions = True

    def __init__(self, mutations: MutationCollector | None = None) -> None:
        """Initialize lexical bindings, optionally reusing collected mutation facts.

        :param MutationCollector | None mutations: Completed mutation facts for a fixing pass.
        """
        super().__init__(mutations)
        self.offenses: set[int] = set()
        self.scopes: list[dict[str, str | None]] = [{}]
        self.scope_kinds = ["module"]
        self.scope_globals: list[set[str]] = [set()]
        self.deferred: list[
            tuple[
                ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.GeneratorExp,
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
        # Deferred bodies use enclosing bindings after those scopes finish defining names.
        # This also avoids rewriting aliases that the enclosing scope reassigns later.
        for function, scopes, kinds, globals_ in self.deferred:
            self.scopes, self.scope_kinds = scopes.copy(), kinds.copy()
            self.scope_globals = globals_.copy()
            if isinstance(function, ast.GeneratorExp):
                self._visit_comprehension_body(function)
                continue
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

    def _record(self, node: ast.Import | ast.ImportFrom | ast.Name | ast.Attribute) -> None:
        """Record the source line number of an offending node."""
        self.offenses.add(node.lineno)

    def visit_Import(self, node: ast.Import) -> None:
        """Bind imported modules and record direct os.path imports."""
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            origin = alias.name if alias.asname else alias.name.split(".")[0]
            self.scopes[-1][name] = origin
            self.import_origins[name].add(origin)
            if alias.name == "os.path" or alias.name.startswith("os.path."):
                self._record(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Bind from-import aliases and record absolute os.path imports."""
        for alias in node.names:
            origin = f"{node.module}.{alias.name}" if node.module and not node.level else None
            if alias.name == "*":
                # Unknown exports can replace any binding in this namespace.
                # Update in place so deferred bodies see the invalidation too.
                self.scopes[-1].update(dict.fromkeys(self.scopes[-1]))
                if origin and origin.startswith("os.path."):
                    self._record(node)
                continue
            name = alias.asname or alias.name
            self.scopes[-1][name] = origin
            if origin:
                self.import_origins[name].add(origin)
            if origin and (origin == "os.path" or origin.startswith("os.path.")):
                self._record(node)

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
        if self._use_literal_conditions and isinstance(node.test, ast.Constant):
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
        super().visit_Attribute(node)

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

    def _defer_body(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.GeneratorExp
    ) -> None:
        """Queue a body with live enclosing bindings for subsequent analysis.

        :param ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.GeneratorExp node: Deferred body owner.
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

    def visit_For(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        """Visit loop suites without assuming bindings survive repeated iterations.

        :param ast.For | ast.AsyncFor | ast.While node: Loop with an optional else suite.
        """
        if isinstance(node, ast.While):
            header = node.test
        else:
            self.visit(node.iter)
            header = node.target
        before = self.scopes[-1].copy()
        names = _ScopeNames()
        for statement in [header, *node.body]:
            names.visit(statement)
        if "*" in names.names:
            names.names.update(self.scopes[-1])
        self.scopes[-1].update(dict.fromkeys(names.names))
        # A while test always runs; a for target may never be assigned.
        if isinstance(node, ast.While):
            self.visit(header)
        entry = self.scopes[-1].copy()
        if not isinstance(node, ast.While):
            self.visit(header)
        for statement in node.body:
            self.visit(statement)
        body = self.scopes[-1].copy()
        self.merge_bindings([before, entry, body])
        for statement in node.orelse:
            self.visit(statement)
        self.merge_bindings([before, entry, body, self.scopes[-1].copy()])

    visit_AsyncFor = visit_For
    visit_While = visit_For

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
            self._invalidate_mutation(node.target)

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
        """Shadow named or mapping-rest captures and visit nested patterns."""
        name = declaration_name(node)
        if name:
            self.scopes[-1][name] = None
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs
    visit_MatchMapping = visit_MatchAs

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        """Visit the first iterable immediately and defer generator bodies."""
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
        if isinstance(node, ast.GeneratorExp):
            self._defer_body(node)
        else:
            self._visit_comprehension_body(node)

    def _visit_comprehension_body(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        """Visit comprehension targets, filters, and values after its first iterable.

        :param ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp node: Comprehension owner.
        """
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


def lint_file(filepath: Path) -> list[tuple[int, str]]:
    """Read and parse a Python file, propagating read and syntax errors.

    :return list[tuple[int, str]]: One (line, stripped source) per offending line, sorted.
    """
    content = filepath.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(filepath))
    if "os" not in content or "path" not in content:
        return []
    detector = OSPathDetector()
    detector.visit(tree)
    lines = physical_lines(content)
    return [(line, lines[line - 1].strip()) for line in sorted(detector.offenses)]
