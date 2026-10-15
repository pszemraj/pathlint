"""Migrate supported os.path calls using AST locations and source-preserving edits."""

from __future__ import annotations

import ast
import ntpath
import sys
from pathlib import PureWindowsPath

from pathlint._source import _Source, is_docstring
from pathlint.bindings import _ScopeNames, declaration_name, import_binding
from pathlint.edits import _clean_imports, _insert_import, _references_namespace
from pathlint.linter import OSPathDetector
from pathlint.mutations import MutationCollector

REPLACEMENTS = {
    "join": ("", True),
    "isabs": (".is_absolute()", False),
    "basename": (".name", False),
    "dirname": (".parent", True),
    "expanduser": (".expanduser()", True),
}


class _PathFixer(OSPathDetector):
    """Resolve imported calls and collect conservative pathlib source edits."""

    _require_definite_bindings = True

    def __init__(
        self,
        content: str,
        tree: ast.Module,
        mutations: MutationCollector,
        platform: str,
        nodes: tuple[ast.AST, ...],
    ) -> None:
        """Initialize source edits and choose an unused pathlib constructor alias.

        :param str content: Original Python source to edit.
        :param ast.Module tree: Parsed module used to identify names and leading imports.
        :param MutationCollector mutations: Collected import origins, escapes, and writes.
        :param str platform: Target platform used to check path spelling equivalence.
        :param tuple[ast.AST, ...] nodes: Cached nodes used to collect whole-module facts.
        """
        super().__init__(mutations)
        self.mutated_attributes.update(self.escaped_origins)
        self.source = _Source(content)
        self.platform = platform
        self.count = 0
        self.needs_import = False
        self.debug_calls: set[ast.Call] = set()
        # Annotation evaluation can be postponed, including by default on Python 3.14.
        # Keep expressions intact without imposing definition-time bindings on them.
        self.annotation_calls: set[ast.Call] = set()
        # Deferred bodies may run before later imports, or between rebindings.
        # Same-spelling writes in unrelated scopes also exclude an alias: avoiding
        # uncertain migrations needs no execution-order or call-graph analysis.
        # Late str imports and conflicting import origins invalidate deferred calls.
        self.deferred_rebindings.update(
            name
            for name, origins in mutations.import_origins.items()
            if len(origins) > 1 or name == "str"
        )
        # Explicit global writes can affect immediate calls after a deferred body
        # runs, including through nested functions or class suites.
        names: set[str] = set()
        for node in nodes:
            if isinstance(node, ast.Name):
                names.add(node.id)
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    self.deferred_rebindings.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update(import_binding(node, alias)[0] for alias in node.names)
                if isinstance(node, ast.ImportFrom) and node.level:
                    self.deferred_rebindings.update(
                        alias.asname or alias.name for alias in node.names
                    )
            name = declaration_name(node)
            if name:
                names.add(name)
                self.deferred_rebindings.add(name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope_names = _ScopeNames()
                for statement in node.body:
                    scope_names.visit(statement)
                self.global_rebindings.update(scope_names.names & scope_names.globals)
            for annotation in (getattr(node, "annotation", None), getattr(node, "returns", None)):
                if annotation is not None:
                    self.annotation_calls.update(
                        call for call in ast.walk(annotation) if isinstance(call, ast.Call)
                    )
        self.leading_aliases: set[str] = set()
        for index, node in enumerate(tree.body):
            if index == 0 and is_docstring(node):
                continue
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                break
            self.leading_aliases.update(import_binding(node, alias)[0] for alias in node.names)
        self.path_name = "_Path"
        while self.path_name in names:
            self.path_name += "_"

    def constructor(self) -> str:
        """Reuse a visible pathlib constructor or request an import for a new alias.

        :return str: Source expression naming the pathlib Path constructor.
        """
        indices = (len(self.scopes) - 1, 0) if len(self.scopes) > 1 else (0,)
        for index in indices:
            scope = self.scopes[index]
            for name in scope:
                if index == 0 and name not in self.leading_aliases:
                    continue
                origin = self.resolve_name(name)
                if origin == "pathlib.Path":
                    return name
                if origin == "pathlib":
                    return f"{name}.Path"
        self.needs_import = True
        return self.path_name

    def visit_FormattedValue(self, node: ast.FormattedValue) -> None:
        """Analyze debug field expressions without changing their displayed source labels.

        :param ast.FormattedValue node: F-string field with an expression and optional format spec.
        """
        _, end = self.source.span(node.value)
        content = self.source.content
        # Parsed expressions are followed only by grouping, trivia and the field
        # separator. Inspect that gap without copying or tokenizing the file tail.
        while end < len(content):
            char = content[end]
            if char == "#":
                while end < len(content) and content[end] not in "\r\n":
                    end += 1
            elif char in ") \t\f\r\n\\":
                end += 1
            else:
                break
        if content[end : end + 1] == "=":
            self.debug_calls.update(
                call for call in ast.walk(node.value) if isinstance(call, ast.Call)
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Rewrite supported imported os.path calls after processing nested calls.

        :param ast.Call node: Call expression to inspect for a supported transformation.
        """
        origin = self.resolve(node.func)
        self.generic_visit(node)
        if node in self.debug_calls or node in self.annotation_calls:
            return
        if not origin or not origin.startswith("os.path."):
            return
        if any(
            changed == "pathlib" or changed.startswith("pathlib.")
            for changed in self.mutated_attributes
        ):
            # Path also depends on inherited and platform-specific pathlib classes.
            return
        function = origin.removeprefix("os.path.")
        if node.keywords or any(
            not isinstance(arg, ast.Constant) or not isinstance(arg.value, str) for arg in node.args
        ):
            # Unknown values can contain bytes, normalized spellings, or unknown
            # users. Keep the call; eligible literal calls inside it still migrate.
            return
        if function == "join":
            if not node.args:
                return
        elif function not in REPLACEMENTS or len(node.args) != 1:
            return
        values = [arg.value for arg in node.args]
        suffix, wraps_str = REPLACEMENTS[function]
        if function == "isabs" and values[0].startswith(("/", "\\")):
            # Older Windows os.path accepts roots without drives; pathlib does not.
            return
        if wraps_str and (
            any("str" in scope for scope in self.scopes)
            or "str" in self.global_rebindings
            or ("generator" in self.scope_kinds and "str" in self.deferred_rebindings)
        ):
            return
        if wraps_str and any(value.startswith("./") for value in values):
            return
        if (
            self.platform == "win32"
            and function != "isabs"
            and (
                any(
                    ntpath.basename(value) != PureWindowsPath(value).name
                    if function == "basename"
                    else ntpath.dirname(value) != str(PureWindowsPath(value).parent)
                    if function == "dirname"
                    else value != str(PureWindowsPath(value))
                    for value in values
                )
                or (function == "join" and ntpath.join(*values) != str(PureWindowsPath(*values)))
            )
        ):
            # String-returning calls can retain literal spellings normalized by pathlib.
            return
        # These literal spellings have different string results under pathlib.
        if function != "isabs" and any(
            (
                value in {"", ".", ".."}
                or value.endswith(("/", "\\", "/.", "/..", "\\.", "\\.."))
                or "//" in value
                or "/./" in value
            )
            for value in values
        ):
            return
        if function == "dirname" and "/" not in values[0]:
            return
        if function == "expanduser" and values[0].startswith("~"):
            # Home values can contain spelling that pathlib normalizes, and
            # os.path leaves unknown users unchanged where Path.expanduser raises.
            return
        start, end = self.source.span(node)
        function_start, function_end = self.source.span(node.func)
        if "#" in self.source.content[function_start:function_end]:
            return
        constructor = self.constructor()
        # Replace only the callee span, retaining wrapping parentheses and comments.
        call = (
            self.source.content[start:function_start]
            + constructor
            + self.source.content[function_end:end]
        )
        replacement = f"{call}{suffix}"
        if wraps_str:
            replacement = f"str({replacement})"
        # A resolved callee and literal-only arguments contain no nested call edits.
        self.source.edits.append((start, end, replacement))
        self.count += 1


def fix_content(content: str, *, platform: str = sys.platform) -> tuple[str, int]:
    """Rewrite supported imported os.path calls and update their imports.

    Collect attribute mutations before editing calls in deferred function bodies.

    :param str content: Python source to parse and transform.
    :param str platform: Target platform for path spelling checks; defaults to this host.
    :return tuple[str, int]: Updated source and the total call and import replacements.
    """
    tree = ast.parse(content)
    nodes = tuple(ast.walk(tree))
    if _references_namespace(nodes):
        # Dynamic writes can overwrite imported aliases and generated constructors.
        return content, 0
    if any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for node in nodes
    ):
        # Unknown exports can overwrite both imported and generated constructor aliases.
        return content, 0
    if any(
        getattr(node, "type_params", ()) or isinstance(node, getattr(ast, "TypeAlias", ()))
        for node in nodes
    ):
        # Type parameters and lazy aliases use annotation scopes we do not analyze.
        return content, 0
    mutations = MutationCollector()
    mutations.visit(tree)
    fixer = _PathFixer(content, tree, mutations, platform, nodes)
    fixer.visit(tree)
    updated = fixer.source.render(0, len(content))
    updated, import_count = _clean_imports(updated)
    if fixer.needs_import:
        updated = _insert_import(updated, fixer.path_name)
    return updated, fixer.count + import_count
