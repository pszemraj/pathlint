# pathlint

Find `os.path` usage in Python code and migrate supported calls to `pathlib`.

## Example

```python
# Before
import os

filename = os.path.join("src", "file.py")

# With pathlib (keeping a string result)
from pathlib import Path

filename = str(Path("src") / "file.py")
```

Find places to migrate, then preview the changes pathlint can make:

```bash
pathlint check .
pathlint fix . --dry-run
```

## Installation

Requires **Python 3.9+**. No runtime dependencies.

```bash
pip install pathlint
pathlint --version
```

## Quick start

Check a project, a directory, or individual files:

```bash
pathlint check .
pathlint check src/ tests/
pathlint check myfile.py
```

Diagnostics point to the source line so you can review each use:

```text
src/utils.py:12: PL001 os.path usage: os.path.exists(value)
Found 1 violation in 1 of 42 files checked.
```

Preview experimental fixes without changing files:

```bash
pathlint fix . --dry-run
```

For a file containing `import os` and the join call, the preview shows:

```diff
--- example.py
+++ example.py (fixed)
@@ -1,2 +1,3 @@
 import os
-filename = os.path.join("src", "file.py")
+from pathlib import Path as _Path
+filename = str(_Path("src", "file.py"))
```

**Review the diff before applying it, then run your tests:**

```bash
pathlint fix .
```

After applying fixes, rerun `pathlint check .` to see which uses need
manual migration.

List the Python files that any of these commands would select:

```bash
pathlint files .
```

Use this listing to confirm your exclusions before applying fixes; it selects
files without reading their Python source.

Paths default to the current directory. Use `pathlint --help` or
`pathlint check --help` to explore the CLI; `python -m pathlint check .` also
works when you prefer module invocation.

## What it detects

```python
import os
import os.path as osp
from os.path import exists as present

os.path.exists("file")
osp.join("directory", "file")
present("file")
callback = os.path.join
```

Pathlint resolves imports, aliases, and local shadowing, including usage in
annotations. Valid `os.PathLike` annotations are not flagged. Detection is
static: dynamic rebinding and wildcard imports may leave uses undetected.
See [detection details](docs/detection.md) for advanced cases.

## Auto-fix

**Fixing is experimental. Review every diff and run your tests.**

Only these five calls support automatic fixes, with string-literal arguments:

| Usage | Pathlib replacement |
| --- | --- |
| `join` | `str(Path(...))` |
| `basename` | `Path(...).name` |
| `dirname` | `str(Path(...).parent)` |
| `isabs` | `Path(...).is_absolute()` |
| `expanduser` (without `~`) | `str(Path(...).expanduser())` |
| `exists`, `isfile`, `isdir` | Manual migration |
| `abspath`, `splitext`, `sep`, `pathsep` | Manual migration |

Fixes preserve surrounding comments and strings. Annotation usages are detected
but never rewritten.

The fixer is conservative: it rewrites calls only when its static checks allow
them and leaves uncertain cases unchanged. It does not fix every violation.
See [fixer limitations](docs/fixer.md) for skipped calls and path forms.

## Common options

Exclude directories or generated files; repeat `--exclude` / `-e` as needed:

```bash
pathlint check . --exclude vendor -e "*.generated.py"
```

Directory scans respect Git ignore rules when Git is available and skip common
generated directories by default. Preview selection with `pathlint files .`;
see [file-selection rules](docs/file-selection.md) for exact matching behavior.

```bash
# Findings only, without a summary
pathlint check . --quiet

# Five files with the most violations
pathlint check . --stats

# Machine-readable report
pathlint check . --output-format json

# Fix preview without a summary or terminal colors
pathlint fix . --dry-run --quiet --no-color
```

For scripting, `check` exits `0` when clean and `1` when it finds violations;
`fix --dry-run` exits `1` when it proposes edits. Errors exit `2`.
See the [CLI reference](docs/cli.md) for output formats, exit codes, and
migration from older commands.

## Pre-commit

Add to `.pre-commit-config.yaml`, pinning the release you want to use:

```yaml
repos:
  - repo: https://github.com/pszemraj/pathlint
    rev: v0.3.0
    hooks:
      - id: pathlint
        args: [--exclude, vendor, --exclude, backups]
```

Install and run the hook:

```bash
pre-commit install
pre-commit run pathlint --all-files
```

The `pathlint` hook runs `pathlint check`. Use `pathlint-fix` instead to apply
experimental fixes, and review its changes before committing.

## Development

Install the development dependencies, run the tests, and check style:

```bash
pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
```

## License

MIT License - see [LICENSE.txt](LICENSE.txt).
