#!/usr/bin/env python3
"""Migrate supported os.path calls using AST locations and source-preserving edits."""

from __future__ import annotations

import ast
import difflib
import sys
from pathlib import Path

from pathlint.linter import OSPathDetector, _ScopeNames

METHODS = {
    "exists": "exists()",
    "isfile": "is_file()",
    "isdir": "is_dir()",
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
        self.lines = content.splitlines(keepends=True)
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
        super().__init__(content.splitlines())
        self.source = _Source(content)
        self.count = 0
        self.needs_import = False
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
        self.path_name = "Path"
        while self.path_name in names:
            self.path_name = "PathlintPath" if self.path_name == "Path" else self.path_name + "_"

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

    def visit_Try(self, node: ast.Try) -> None:
        """Merge successful and interrupted bindings before visiting the final suite.

        :param ast.Try node: Try statement whose suites may establish different bindings.
        """
        scope = self.scopes[-1]
        before = scope.copy()
        for statement in node.body:
            self.visit(statement)
        successful = scope.copy()
        self.merge_bindings([before, successful])
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

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        """Visit for suites without assuming bindings stay unchanged across iterations.

        :param ast.For | ast.AsyncFor node: Synchronous or asynchronous for statement.
        """
        self.visit(node.iter)
        before = self.scopes[-1].copy()
        names = _ScopeNames()
        for statement in [node.target, *node.body]:
            names.visit(statement)
        self.scopes[-1].update(dict.fromkeys(names.names))
        entry = self.scopes[-1].copy()
        self.visit(node.target)
        for statement in node.body:
            self.visit(statement)
        body = self.scopes[-1].copy()
        self.merge_bindings([before, entry, body])
        for statement in node.orelse:
            self.visit(statement)
        self.merge_bindings([before, entry, body, self.scopes[-1].copy()])

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        """Visit while suites without assuming bindings stay unchanged across iterations.

        :param ast.While node: While statement whose body may never execute.
        """
        before = self.scopes[-1].copy()
        names = _ScopeNames()
        for statement in [node.test, *node.body]:
            names.visit(statement)
        self.scopes[-1].update(dict.fromkeys(names.names))
        self.visit(node.test)
        entry = self.scopes[-1].copy()
        for statement in node.body:
            self.visit(statement)
        body = self.scopes[-1].copy()
        self.merge_bindings([before, entry, body])
        for statement in node.orelse:
            self.visit(statement)
        self.merge_bindings([before, entry, body, self.scopes[-1].copy()])

    def visit_Match(self, node: ast.AST) -> None:
        """Merge each match case with the possibility that no case executes.

        :param ast.AST node: Match statement with a subject and case suites.
        """
        self.visit(node.subject)
        scope = self.scopes[-1]
        before = scope.copy()
        alternatives = [before]
        for case in node.cases:
            scope.clear()
            scope.update(before)
            self.visit(case)
            alternatives.append(scope.copy())
        self.merge_bindings(alternatives)

    def visit_Call(self, node: ast.Call) -> None:
        """Rewrite supported imported os.path calls after processing nested calls.

        :param ast.Call node: Call expression to inspect for a supported transformation.
        """
        origin = self.resolve(node.func)
        self.generic_visit(node)
        if not origin or not origin.startswith("os.path."):
            return
        function = origin.removeprefix("os.path.")
        if node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
            return
        if any(isinstance(arg, ast.Constant) and isinstance(arg.value, bytes) for arg in node.args):
            return
        if function == "join":
            if not node.args:
                return
        elif function not in METHODS or len(node.args) != 1:
            return
        if function in {"join", "dirname", "expanduser"} and any(
            "str" in scope for scope in self.scopes
        ):
            return
        # These literal spellings have different string results under pathlib.
        if function in {"basename", "dirname", "join"} and any(
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
            and not any(separator in node.args[0].value for separator in ("/", "\\"))
        ):
            return
        start, end = self.source.span(node)
        _, function_end = self.source.span(node.func)
        arguments = self.source.render(function_end, end)
        constructor = self.constructor()
        if function == "join":
            replacement = f"str({constructor}{arguments})"
        else:
            replacement = f"{constructor}{arguments}.{METHODS[function]}"
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
    lines = content.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in content else "\n"
    insertion = tree.body[0].lineno - 1 if tree.body else len(lines)
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
                        line_end = content.find("\n", offset)
                        offset = len(content) if line_end == -1 else line_end + 1
                prefix = content[:offset].rstrip("; \t")
                if prefix and not prefix.endswith("\n"):
                    prefix += newline
                statement = "from pathlib import Path" + (f" as {name}" if name != "Path" else "")
                return prefix + statement + newline + content[offset:]
            break
    statement = "from pathlib import Path"
    if name != "Path":
        statement += f" as {name}"
    prefix = "".join(lines[:insertion])
    if prefix and not prefix.endswith("\n"):
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


def _clean_imports(content: str) -> tuple[str, int]:
    """Remove unused os.path import aliases without discarding import comments.

    :param str content: Python source after call transformations.
    :return tuple[str, int]: Updated source and the count of aliases removed or simplified.
    """
    tree = ast.parse(content)
    source = _Source(content)
    loaded = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
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
                    and name in loaded
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
            if removable and name not in loaded:
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
            line_end = content.find("\n", end)
            line_end = len(content) if line_end == -1 else line_end
            following = content[end:line_end]
            if following.lstrip().startswith(";"):
                end += len(following) - len(following.lstrip()) + 1
                while end < len(content) and content[end] in " \t":
                    end += 1
            else:
                line_start = content.rfind("\n", 0, start) + 1
                preceding = content[line_start:start]
                if preceding.rstrip().endswith(";"):
                    start = line_start + len(preceding.rstrip()) - 1
                elif content[line_start:start].strip() == "" and following.strip() == "":
                    start = line_start
                    end = min(line_end + 1, len(content))
                else:
                    # An empty suite still needs a statement.
                    replacement = "pass"
            # Removing the last statement of an indented suite requires pass.
            if node.col_offset and replacement == "":
                replacement = "pass"
                start, end = source.span(node)
        source.replace(start, end, replacement)
        count += node_count
    return source.render(0, len(content)), count


def fix_content(content: str) -> tuple[str, int]:
    """Rewrite supported imported os.path calls and update their imports.

    :param str content: Python source to parse and transform.
    :return tuple[str, int]: Updated source and the total call and import replacements.
    """
    tree = ast.parse(content)
    fixer = _PathFixer(content, tree)
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
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
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
