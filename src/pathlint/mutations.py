"""Collect conservative import aliases, escapes, and attribute mutations."""

from __future__ import annotations

import ast
from collections import defaultdict

from pathlint.bindings import _ScopeNames, import_binding


class MutationCollector(ast.NodeVisitor):
    """Collect possible mutations without resolving lexical read bindings."""

    def __init__(self, facts: MutationCollector | None = None) -> None:
        """Initialize mutation facts or copy an already collected module graph.

        :param MutationCollector | None facts: Completed facts to reuse without recollecting aliases.
        """
        self.mutated_attributes = set(facts.mutated_attributes) if facts else set()
        self.escaped_origins = set(facts.escaped_origins) if facts else set()
        self.import_origins: dict[str, set[str]] = defaultdict(set)
        self.mutation_aliases: dict[str, set[str]] = defaultdict(set)
        if facts:
            self.import_origins.update(
                (name, set(origins)) for name, origins in facts.import_origins.items()
            )
            self.mutation_aliases.update(
                (name, set(origins)) for name, origins in facts.mutation_aliases.items()
            )
        self._aliases_precollected = facts is not None

    def visit_Module(self, node: ast.Module) -> None:
        """Collect all possible copies before inspecting attribute writes."""
        self._collect_mutation_aliases(node)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import | ast.ImportFrom) -> None:
        """Retain named absolute import origins without inventing wildcard exports."""
        for alias in node.names:
            name, origin = import_binding(node, alias)
            if origin and alias.name != "*":
                self.import_origins[name].add(origin)

    visit_ImportFrom = visit_Import

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Collect writes after visiting the attribute base expression."""
        self.generic_visit(node)
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._invalidate_mutation(node)

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
        if self._aliases_precollected:
            return
        copies: list[tuple[ast.AST | None, ast.AST]] = []
        for child in ast.walk(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    name, origin = import_binding(child, alias)
                    if origin:
                        self.mutation_aliases[name].add(origin)
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

    def _invalidate_mutation(self, node: ast.Attribute) -> None:
        """Invalidate possible imported origins affected by an attribute write.

        :param ast.Attribute node: Assigned or deleted attribute target.
        """
        # Retain earlier origins conservatively for mutations, never ordinary reads.
        for possible in self._mutation_origins(node):
            if possible in {"os.__class__", "os.path.__class__", "pathlib.__class__"}:
                # Module subclasses can override every attribute with descriptors.
                self.mutated_attributes.add(possible.removesuffix(".__class__"))
            elif possible == "os.path" or possible.startswith(("os.path.", "pathlib.")):
                self.mutated_attributes.add(possible)
