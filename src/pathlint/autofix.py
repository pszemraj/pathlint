#!/usr/bin/env python3
"""Migrate supported os.path calls using AST locations and source-preserving edits."""

from __future__ import annotations

import ast
import difflib
import ntpath
import sys
import tokenize
from io import StringIO
from pathlib import Path, PureWindowsPath

from pathlint.linter import OSPathDetector, _ScopeNames

METHODS = {
    "isabs": "is_absolute()",
    "basename": "name",
    "dirname": "parent",
    "expanduser": "expanduser()",
}


class _Source:
    """Track source offsets and replacements without rebuilding the syntax tree."""

    def __init__(self, content: str) -> None:
        """Index the original source and initialize an empty edit collection.

        :param str content: Python source whose original character offsets identify edits.
        """
        self.content = content
        self.lines = list(StringIO(content, newline=""))
        self.starts = [0]
        for line in self.lines:
            self.starts.append(self.starts[-1] + len(line))
        self.edits: list[tuple[int, int, str]] = []

    def offset(self, line: int, column: int) -> int:
        """Translate an AST line and UTF-8 byte column into a character offset.

        :param int line: One-based source line number.
        :param int column: Zero-based UTF-8 byte offset within that line.
        :return int: Character offset into the original source.
        """
        return self.starts[line - 1] + len(
            self.lines[line - 1].encode("utf-8")[:column].decode("utf-8")
        )

    def span(self, node: ast.AST) -> tuple[int, int]:
        """Locate the original source interval occupied by a positioned AST node.

        :param ast.AST node: Syntax node with start and end location attributes.
        :return tuple[int, int]: Start and exclusive end character offsets.
        """
        return (
            self.offset(node.lineno, node.col_offset),
            self.offset(node.end_lineno, node.end_col_offset),
        )

    def render(self, start: int, end: int) -> str:
        """Apply edits contained within an original source interval.

        :param int start: Inclusive character offset in the original source.
        :param int end: Exclusive character offset in the original source.
        :return str: Source interval with its contained replacements applied.
        """
        text = self.content[start:end]
        for left, right, replacement in sorted(self.edits, reverse=True):
            if start <= left and right <= end:
                text = text[: left - start] + replacement + text[right - start :]
        return text

    def replace(self, start: int, end: int, replacement: str) -> None:
        """Record a replacement, superseding edits contained within its interval.

        :param int start: Inclusive character offset in the original source.
        :param int end: Exclusive character offset in the original source.
        :param str replacement: Text to substitute for the interval.
        """
        self.edits = [edit for edit in self.edits if not (start <= edit[0] and edit[1] <= end)]
        self.edits.append((start, end, replacement))


class _PathFixer(OSPathDetector):
    """Resolve imported calls and collect conservative pathlib source edits."""

    def __init__(self, content: str, tree: ast.Module) -> None:
        """Initialize source edits and choose an unused pathlib constructor alias.

        :param str content: Original Python source to edit.
        :param ast.Module tree: Parsed module used to identify names and leading imports.
        """
        super().__init__(list(StringIO(content, newline="")))
        self.source = _Source(content)
        self.count = 0
        self.needs_import = False
        self.debug_calls: set[ast.Call] = set()
        # Annotation evaluation can be postponed, including by default on Python 3.14.
        # Keep expressions intact without imposing definition-time bindings on them.
        self.annotation_calls = {
            call
            for node in ast.walk(tree)
            for annotation in (getattr(node, "annotation", None), getattr(node, "returns", None))
            if annotation is not None
            for call in ast.walk(annotation)
            if isinstance(call, ast.Call)
        }
        # Deferred bodies may run before later imports, or between rebindings.
        # Same-spelling writes in unrelated scopes also exclude an alias: avoiding
        # uncertain migrations needs no execution-order or call-graph analysis.
        self.deferred_rebindings = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
        }
        self.deferred_rebindings.update(
            name
            for node in ast.walk(tree)
            if not isinstance(node, ast.alias)
            for name in (getattr(node, "name", None), getattr(node, "rest", None))
            if isinstance(name, str)
        )
        self.deferred_rebindings.update(
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level
            for alias in node.names
        )
        # Explicit global writes can affect immediate calls after a deferred body
        # runs, including through nested functions or class suites.
        self.global_rebindings: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope_names = _ScopeNames()
                for statement in node.body:
                    scope_names.visit(statement)
                self.global_rebindings.update(scope_names.names & scope_names.globals)
        names = (
            {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            | {node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)}
            | {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            }
            | {
                alias.asname or alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
            }
        )
        names.update(
            name
            for node in ast.walk(tree)
            for name in (getattr(node, "name", None), getattr(node, "rest", None))
            if isinstance(name, str)
        )
        self.leading_aliases: set[str] = set()
        for index, node in enumerate(tree.body):
            if (
                index == 0
                and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                continue
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                break
            self.leading_aliases.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        self.path_name = "_Path"
        while self.path_name in names:
            self.path_name += "_"

    def _defer_body(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.GeneratorExp
    ) -> None:
        """Freeze certain enclosing imports without borrowing later bindings.

        :param ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.GeneratorExp node: Deferred body owner.
        """
        scopes = [{**scope, **dict.fromkeys(self.deferred_rebindings)} for scope in self.scopes]
        self.deferred.append((node, scopes, self.scope_kinds.copy(), self.scope_globals.copy()))

    def resolve_name(self, name: str) -> str | None:
        """Exclude explicit global writes and aliases rebound around deferred bodies.

        :return str | None: Import spelling, or None for a rebound or unresolved name.
        """
        if name in self.global_rebindings:
            return None
        if name in self.deferred_rebindings and any(
            kind in {"function", "generator"} for kind in self.scope_kinds
        ):
            return None
        return super().resolve_name(name)

    def constructor(self) -> str:
        """Reuse a visible pathlib constructor or request an import for a new alias.

        :return str: Source expression naming the pathlib Path constructor.
        """
        for index in range(len(self.scopes) - 1, -1, -1):
            scope = self.scopes[index]
            for name in scope:
                if (index == 0 and name not in self.leading_aliases) or (
                    index != 0 and index != len(self.scopes) - 1
                ):
                    continue
                origin = self.resolve_name(name)
                if origin == "pathlib.Path":
                    return name
                if origin == "pathlib":
                    return f"{name}.Path"
        self.needs_import = True
        return self.path_name

    def visit_If(self, node: ast.If) -> None:
        """Visit both branches independently and merge their resulting bindings.

        :param ast.If node: Conditional statement to inspect for bindings and calls.
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
        self.merge_bindings([body, alternative])

    def visit_Try(self, node: ast.Try) -> None:
        """Merge successful and interrupted bindings before visiting the final suite.

        :param ast.Try node: Try statement whose suites may establish different bindings.
        """
        scope = self.scopes[-1]
        before = scope.copy()
        for statement in node.body:
            self.visit(statement)
        successful = scope.copy()
        names = _ScopeNames()
        for statement in (*node.body, *node.handlers, *node.orelse):
            names.visit(statement)
        uncertain = before.copy()
        uncertain.update(dict.fromkeys(names.names))
        # Exceptions can interrupt any binding before a later import restores it.
        self.merge_bindings([before, successful, uncertain])
        interrupted = scope.copy()
        alternatives = []
        for handler in node.handlers:
            scope.clear()
            scope.update(interrupted)
            self.visit(handler)
            alternatives.append(scope.copy())
        scope.clear()
        scope.update(successful)
        for statement in node.orelse:
            self.visit(statement)
        alternatives.append(scope.copy())
        if node.finalbody:
            alternatives.append(interrupted)
        self.merge_bindings(alternatives)
        for statement in node.finalbody:
            self.visit(statement)

    visit_TryStar = visit_Try

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        """Visit reachable calls without assuming every context binding completes.

        :param ast.With | ast.AsyncWith node: Context statement that may suppress exceptions.
        """
        self.generic_visit(node)
        names = _ScopeNames()
        names.visit(node)
        # An earlier context can suppress failures in later entries or body statements.
        self.scopes[-1].update(dict.fromkeys(names.names))

    visit_AsyncWith = visit_With

    def visit_Match(self, node: ast.AST) -> None:
        """Merge case bodies and bindings retained when patterns or guards fail.

        :param ast.AST node: Match statement with a subject and case suites.
        """
        self.visit(node.subject)
        scope = self.scopes[-1]
        before = scope.copy()
        fallthrough = before
        alternatives = [before]
        for case in node.cases:
            scope.clear()
            scope.update(fallthrough)
            self.visit(case.pattern)
            if case.guard:
                self.visit(case.guard)
            captured = scope.copy()
            for statement in case.body:
                self.visit(statement)
            alternatives.append(scope.copy())
            # Captures and guard assignments can survive a failed guard. A taken
            # case body, however, never flows into the next case.
            self.merge_bindings([fallthrough, captured])
            fallthrough = scope.copy()
        alternatives.append(fallthrough)
        self.merge_bindings(alternatives)

    def visit_FormattedValue(self, node: ast.FormattedValue) -> None:
        """Analyze debug field expressions without changing their displayed source labels.

        :param ast.FormattedValue node: F-string field with an expression and optional format spec.
        """
        _, end = self.source.span(node.value)
        tokens = tokenize.generate_tokens(
            StringIO(self.source.content[end:], newline=None).readline
        )
        following = next(
            token.string for token in tokens if token.type == tokenize.OP and token.string != ")"
        )
        if following == "=":
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
        elif function not in METHODS or len(node.args) != 1:
            return
        if (
            function == "isabs"
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value.startswith(("/", "\\"))
        ):
            # Older Windows os.path accepts roots without drives; pathlib does not.
            return
        if function in {"join", "dirname", "expanduser"} and (
            any("str" in scope for scope in self.scopes)
            or "str" in self.global_rebindings
            or ("generator" in self.scope_kinds and "str" in self.deferred_rebindings)
        ):
            return
        if function in {"join", "dirname", "expanduser"} and any(
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and arg.value.startswith("./")
            for arg in node.args
        ):
            return
        if sys.platform == "win32" and function in {"join", "basename", "dirname", "expanduser"}:
            literal_args = [
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ]
            if any(
                ntpath.basename(value) != PureWindowsPath(value).name
                if function == "basename"
                else ntpath.dirname(value) != str(PureWindowsPath(value).parent)
                if function == "dirname"
                else value != str(PureWindowsPath(value))
                for value in literal_args
            ) or (
                function == "join"
                and len(literal_args) == len(node.args)
                and ntpath.join(*literal_args) != str(PureWindowsPath(*literal_args))
            ):
                # String-returning calls can retain literal spellings normalized by pathlib.
                return
        # These literal spellings have different string results under pathlib.
        if function != "isabs" and any(
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and (
                arg.value in {"", ".", ".."}
                or arg.value.endswith(("/", "\\", "/.", "/..", "\\.", "\\.."))
                or "//" in arg.value
                or "/./" in arg.value
            )
            for arg in node.args
        ):
            return
        if (
            function == "dirname"
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and "/" not in node.args[0].value
        ):
            return
        if (
            function == "expanduser"
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value.startswith("~")
        ):
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
            self.source.render(start, function_start)
            + constructor
            + self.source.render(function_end, end)
        )
        if function == "join":
            replacement = f"str({call})"
        else:
            replacement = f"{call}.{METHODS[function]}"
            if function in {"dirname", "expanduser"}:
                replacement = f"str({replacement})"
        self.source.replace(start, end, replacement)
        self.count += 1


def _insert_import(content: str, name: str = "Path") -> str:
    """Insert a Path import after the module header while preserving newline style.

    :param str content: Python source to receive the import.
    :param str name: Local name to bind, using an alias when it differs from Path.
    :return str: Source with the import before executable statements and after headers.
    """
    tree = ast.parse(content)
    lines = list(StringIO(content, newline=""))
    newline = "\r\n" if "\r\n" in content else "\r" if "\r" in content else "\n"
    insertion = tree.body[0].lineno - 1 if tree.body else len(lines)
    if tree.body and getattr(tree.body[0], "decorator_list", []):
        # Decorator expressions can start below their opening @(... line.
        insertion = next(index for index, line in enumerate(lines) if line.lstrip().startswith("@"))
    for index, node in enumerate(tree.body):
        if (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ) or isinstance(node, (ast.Import, ast.ImportFrom)):
            insertion = node.end_lineno
        else:
            if node.lineno <= insertion:
                # A leading import shares its physical line with executable code.
                # Insert before ordinary imports, but after docstrings/future imports.
                header = None
                for header_index, candidate in enumerate(tree.body):
                    if (
                        header_index == 0
                        and isinstance(candidate, ast.Expr)
                        and isinstance(candidate.value, ast.Constant)
                        and isinstance(candidate.value.value, str)
                    ) or (
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
                    rest = content[offset:]
                    if rest.lstrip(" \t").startswith(";"):
                        offset += len(rest) - len(rest.lstrip(" \t")) + 1
                        while offset < len(content) and content[offset] in " \t":
                            offset += 1
                    else:
                        offset = source.starts[header.end_lineno]
                prefix = content[:offset].rstrip("; \t")
                if prefix and not prefix.endswith(("\r", "\n")):
                    prefix += newline
                statement = "from pathlib import Path" + (f" as {name}" if name != "Path" else "")
                return prefix + statement + newline + content[offset:]
            break
    statement = "from pathlib import Path"
    if name != "Path":
        statement += f" as {name}"
    prefix = "".join(lines[:insertion])
    if prefix and not prefix.endswith(("\r", "\n")):
        prefix += newline
    return prefix + statement + newline + "".join(lines[insertion:])


def add_pathlib_import(content: str) -> str:
    """Add a Path import unless a top-level pathlib or Path import already exists.

    :param str content: Python source to inspect and update.
    :return str: Original source when an import exists, or source with a Path import added.
    """
    tree = ast.parse(content)
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "pathlib"
            and any(alias.name == "Path" for alias in node.names)
        ) or (
            isinstance(node, ast.Import) and any(alias.name == "pathlib" for alias in node.names)
        ):
            return content
    return _insert_import(content)


def _references_namespace(tree: ast.AST, *, include_dir: bool = False) -> bool:
    """Find references exposing runtime namespaces or dynamic code execution.

    :param ast.AST tree: Parsed source to inspect, including nested scopes.
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
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name in attribute_mutators
            or (not node.level and alias.name in namespace_imports.get(node.module, ()))
        }
    )
    namespace_names.update(
        alias.asname or alias.name
        for node in ast.walk(tree)
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
        for node in ast.walk(tree)
    )


def _clean_imports(content: str) -> tuple[str, int]:
    """Remove unused os.path import aliases without discarding import comments.

    :param str content: Python source after call transformations.
    :return tuple[str, int]: Updated source and the count of aliases removed or simplified.
    """
    tree = ast.parse(content)
    source = _Source(content)
    if any(
        (isinstance(node, ast.Name) and node.id == "__all__")
        or (isinstance(node, ast.alias) and (node.asname or node.name) == "__all__")
        for node in ast.walk(tree)
    ):
        # Explicit exports can refer to imports by string, including imported lists.
        return content, 0
    if any(
        getattr(node, "annotation", None) is not None
        or getattr(node, "returns", None) is not None
        or (isinstance(node, ast.Name) and node.id == "__annotations__")
        or (isinstance(node, ast.Attribute) and node.attr == "__annotations__")
        for node in ast.walk(tree)
    ):
        # Annotation aliases can resolve to strings that need imports after loading.
        return content, 0
    if _references_namespace(tree, include_dir=True):
        # Namespace inspectors and dynamic code can access arbitrary imported bindings.
        return content, 0
    # A later del still needs its imported binding after calls are rewritten.
    referenced = {
        node.id
        for node in ast.walk(tree)
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
        for node in ast.walk(tree)
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
    )
    # Global imports publish module bindings; nonlocal imports require their owner.
    referenced.update(
        name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    )
    for node in ast.walk(tree):
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
        for node in ast.walk(tree)
    )
    count = 0
    for node in ast.walk(tree):
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
            if following.lstrip().startswith(";"):
                end += len(following) - len(following.lstrip()) + 1
                while end < len(content) and content[end] in " \t":
                    end += 1
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


def fix_content(content: str) -> tuple[str, int]:
    """Rewrite supported imported os.path calls and update their imports.

    Collect attribute mutations before editing calls in deferred function bodies.

    :param str content: Python source to parse and transform.
    :return tuple[str, int]: Updated source and the total call and import replacements.
    """
    tree = ast.parse(content)
    if _references_namespace(tree):
        # Dynamic writes can overwrite imported aliases and generated constructors.
        return content, 0
    if any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for node in ast.walk(tree)
    ):
        # Unknown exports can overwrite both imported and generated constructor aliases.
        return content, 0
    if any(
        getattr(node, "type_params", ()) or isinstance(node, getattr(ast, "TypeAlias", ()))
        for node in ast.walk(tree)
    ):
        # Type parameters and lazy aliases use annotation scopes we do not analyze.
        return content, 0
    detector = OSPathDetector(list(StringIO(content, newline="")))
    detector.visit(tree)
    fixer = _PathFixer(content, tree)
    fixer.mutated_attributes.update(detector.mutated_attributes)
    fixer.mutated_attributes.update(detector.escaped_origins)
    # Late str imports can shadow conversions inserted into deferred bodies.
    fixer.deferred_rebindings.update(
        name
        for name, origins in detector.import_origins.items()
        if len(origins) > 1 or name == "str"
    )
    fixer.visit(tree)
    updated = fixer.source.render(0, len(content))
    updated, import_count = _clean_imports(updated)
    if fixer.needs_import:
        updated = _insert_import(updated, fixer.path_name)
    return updated, fixer.count + import_count


def fix_file(filepath: Path, dry_run: bool = False) -> int:
    """Fix one file or print its preview, reporting expected errors to stderr.

    :param Path filepath: UTF-8 Python source file to transform.
    :param bool dry_run: Print a unified diff instead of writing changes.
    :return int: Proposed replacement count, or zero on read, parse, or write failure.
    """
    try:
        original = filepath.read_text(encoding="utf-8")
        updated, count = fix_content(original)
        if count and not dry_run:
            filepath.write_text(updated, encoding="utf-8")
    except (OSError, UnicodeError, SyntaxError) as error:
        print(f"Cannot fix {filepath}: {error}", file=sys.stderr)
        return 0
    if count:
        if dry_run:
            for line in difflib.unified_diff(
                list(StringIO(original, newline="")),
                list(StringIO(updated, newline="")),
                fromfile=str(filepath),
                tofile=f"{filepath} (fixed)",
            ):
                print(line.rstrip("\n"))
        else:
            print(f"Fixed {filepath}: {count} replacements.")
    return count


def main(argv: list[str] | None = None) -> int:
    """Forward the original autofix module entry point to the fix command.

    :param list[str] | None argv: Arguments, defaulting to the current process arguments.
    :return int: Exit status returned by the fix command.
    """
    from pathlint.cli import main as cli_main

    return cli_main(["fix", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    sys.exit(main())
