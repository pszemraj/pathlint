"""Fast linter to detect os.path usage and encourage pathlib adoption."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pathlint")
except PackageNotFoundError:
    __version__ = "unknown"
