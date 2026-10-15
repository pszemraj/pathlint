"""Source text utilities that preserve Python's physical line boundaries."""

from __future__ import annotations

import ast
from io import StringIO


def physical_lines(text: str) -> list[str]:
    """Split on LF, CR, and CRLF only, matching AST line numbering.

    :param str text: Source text whose line endings must be retained.
    :return list[str]: Physical lines, including their original terminators.
    """
    return list(StringIO(text, newline=""))


def is_docstring(node: ast.AST) -> bool:
    """Identify a standalone string expression used as a module header.

    :param ast.AST node: Syntax node to inspect.
    :return bool: Whether the node is a string expression.
    """
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


class _Source:
    """Track source offsets and replacements without rebuilding the syntax tree."""

    def __init__(self, content: str) -> None:
        """Index the original source and initialize an empty edit collection.

        :param str content: Python source whose original character offsets identify edits.
        """
        self.content = content
        self.lines = physical_lines(content)
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
        chunks: list[str] = []
        cursor = start
        for left, right, replacement in sorted(self.edits):
            if start <= left and right <= end:
                chunks.extend((self.content[cursor:left], replacement))
                cursor = right
        chunks.append(self.content[cursor:end])
        return "".join(chunks)

    def replace(self, start: int, end: int, replacement: str) -> None:
        """Record a replacement, superseding edits contained within its interval.

        :param int start: Inclusive character offset in the original source.
        :param int end: Exclusive character offset in the original source.
        :param str replacement: Text to substitute for the interval.
        """
        self.edits = [edit for edit in self.edits if not (start <= edit[0] and edit[1] <= end)]
        self.edits.append((start, end, replacement))
