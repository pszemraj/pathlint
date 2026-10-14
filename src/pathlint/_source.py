"""Source text utilities that preserve Python's physical line boundaries."""

from io import StringIO


def physical_lines(text: str) -> list[str]:
    """Split on LF, CR, and CRLF only, matching AST line numbering.

    :param str text: Source text whose line endings must be retained.
    :return list[str]: Physical lines, including their original terminators.
    """
    return list(StringIO(text, newline=""))
