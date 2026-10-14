# pathlint

[![PyPI version](https://badge.fury.io/py/pathlint.svg)](https://pypi.org/project/pathlint/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

Find `os.path` usage and migrate supported calls to `pathlib`. Pathlint has a
standard-library-only runtime and supports Python 3.9 and newer.

- Detect imports, aliases, function references, calls, and usage in annotations.
- Exclude files and directories with repeatable name or path patterns.
- Honor Git ignore rules automatically when Git is available.
- Get concise text diagnostics, quiet output, or JSON reports.
- Preview experimental fixes as unified diffs before applying them.

## Installation

```bash
pip install pathlint

# Verify installation
pathlint --version

# Check Python files in the current directory
pathlint check .
```

Git is optional. Without Git, directory scans use built-in and explicit
exclusions; `.gitignore` rules are not interpreted.

## Usage

```bash
# Check the current directory (also the behavior of bare pathlint)
pathlint check

# Check one or more files or directories
pathlint check myfile.py
pathlint check src/ tests/ scripts/

# See exactly which files will be selected, without parsing them
pathlint files .

# Preview fixes, then apply them
pathlint fix src/ --dry-run
pathlint fix src/

# Module invocation works too
python -m pathlint check .
```

Use `pathlint --help` or `pathlint check --help` for command help.

### Excluding files and directories

All three commands use the same selection rules:

```bash
pathlint check . \
  --exclude vendor \
  --exclude 'training/wandb/*' \
  --exclude backups

pathlint files . -e '*.generated.py' -e 'src/legacy/'
pathlint fix . --exclude vendor --dry-run

# Disable Git ignores or built-in exclusions independently
pathlint check . --no-gitignore
pathlint check . --no-default-excludes
```

Each `-e` / `--exclude` accepts one pattern; repeat the argument for multiple
patterns. Explicit exclusions add to the built-in exclusions.

- Names without `/`, such as `backups` or `test_*.py`, match at any depth.
- Patterns containing `/` match paths relative to the working directory.
  `./vendor` matches the top-level `vendor`, while `vendor` matches that name
  anywhere. Absolute patterns can exclude paths outside the working directory.
- Directory matches exclude their descendants. A trailing `/` restricts a
  pattern to directories.
- Patterns use case-sensitive `fnmatch` syntax: `*`, `?`, and bracket expressions.
  `*` can span `/`; `**` has no additional meaning. Quote wildcards to prevent
  shell expansion. These patterns do not support Git-style `!` negation.

Built-in exclusions are `__pycache__`, `.venv`, `venv`, `.git`, `node_modules`,
`.tox`, `build`, and `dist`.

In Git worktrees, directory scans include tracked files and untracked files
that Git does not ignore. Git handles nested `.gitignore` rules, negation,
repository-local excludes, and global ignores. Tracked files remain eligible
even when they match an ignore rule. Nonexcluded nested repositories use their
own Git rules.

Explicitly named files bypass Git and built-in exclusions. **Explicit
`--exclude` patterns still apply**, including to filenames supplied by
pre-commit. File symlinks select their resolved Python targets once; exclusions
match both the link and target paths.

### Output

```text
src/utils.py:12: PL001 os.path usage: os.path.exists(value)
Found 1 violation in 1 of 42 files checked.
```

Paths are relative to the working directory when possible, and absolute for
external files. Text output retains the stream's encoding and escapes characters
it cannot represent, including when redirected through legacy Windows encodings.
Each offending source line is reported once, even if it contains several usages.
`PL001` means “Use pathlib instead of os.path.”

```bash
# Findings only; no summary
pathlint check . --quiet

# Show the five files with the most violations
pathlint check . --stats

# Machine-readable report
pathlint check . --output-format json

# Include offender statistics in the JSON report
pathlint check . --output-format json --stats

# Optional aggressive message
pathlint check . --aggressive
```

JSON reports contain `diagnostics`, `errors`, and `summary`. Each diagnostic has
`path`, `line`, `code`, `message`, and `source`. Summary fields are
`files_checked`, `files_with_issues`, `violations`, and `errors`; files that fail
reading or parsing are not counted as checked. `--stats` adds a `statistics`
list containing up to five `{path, violations}` entries.

JSON is available only for `check` and cannot be combined with `--quiet` or
`--aggressive`. Runtime errors are included in the JSON report without mixing
text into stdout. Text-mode errors go to stderr.

### Exit codes and compatibility

| Command | `0` | `1` | `2` |
| --- | --- | --- | --- |
| `check` | Clean or no files selected | Violations found | Input, discovery, read, or parse error |
| `fix` | Completed | — | Input, discovery, read, parse, or write error |
| `fix --dry-run` | No edits proposed | Edits proposed | Error |
| `files` | Listing completed, including empty selection | — | Input or discovery error |

Errors take precedence over violations or proposed edits. Other valid files
are still processed. Ctrl-C exits with status `130`.

Existing invocations continue to work without warnings:

```bash
pathlint .
pathlint --fix src/
pathlint --dry-run src/
```

The original `python -m pathlint.linter` and
`python -m pathlint.autofix` entry points also remain available. A valid empty
selection now succeeds; dry runs now return `1` when they propose edits.

## What it detects

```python
import os
import os as system
import os.path as osp
from os import path as paths
from os.path import exists as present

os.path.exists("file")
system.path.join("directory", "file")
osp.isdir("directory")
paths.dirname(__file__)
present("file")
callback = os.path.join
```

Pathlint resolves imported names in function, class, and comprehension scopes
so ordinary local shadowing is not mistaken for `os.path`. Private aliases in class
scopes are left unresolved because Python mangles their names. Deleted class
locals and cleared exception aliases fall back to module globals. Function bodies
use the enclosing scope's final bindings for detection; dynamic rebinding can
prevent resolution. Detection inspects `if` branches independently and uses literal
conditions when merging bindings in the current scope. Fixes in deferred bodies
require enclosing imports to exist when the function, lambda, or generator is
created, and conservatively skip aliases rebound anywhere in the file, including
imports with conflicting origins. Stable imports inside the body can still be
migrated. This is a static check, not full Python execution analysis.

Expressions in annotations are checked too. Valid `os.PathLike` annotations
are not `os.path` usage and are not flagged. The fixer leaves annotation
expressions unchanged: postponed annotations and Python 3.14's lazy annotations
can resolve names after their bindings change.

## Auto-fix (experimental)

```bash
# Review a unified diff without changing files
pathlint fix src/ --dry-run

# Apply supported migrations
pathlint fix src/

# Print only the diff
pathlint fix src/ --dry-run --quiet

# Disable terminal diff colors
pathlint fix src/ --dry-run --no-color
```

Fixes use AST locations to change expressions while preserving surrounding
source text, including comments and strings. Imported aliases, named function
imports, nested calls, and expressions inside f-strings are supported. Pathlib
imports are placed after module docstrings and future imports, and existing
Pathlib aliases are reused where possible.

Supported calls include `isabs`, `join`, `basename`, `dirname`, and `expanduser`.
Unsupported references retain their imports.
Keyword arguments, starred arguments, call targets containing comments, literal
file descriptors, byte paths, and known inexact literal forms are left unchanged.
These include empty paths, trailing separators, named-user expansion, and rooted
literals passed to `isabs` whose interpretation can differ under Windows path rules.
On Windows, string-returning fixes also skip literals whose separators or dot
components Pathlib would normalize.
Calls with uncertain branch, exception, context manager, match, or loop bindings
and methods whose `os.path` attributes are assigned or deleted are also left
unchanged. Calls outside annotation expressions remain eligible for fixes.
Explicit `global` writes prevent affected aliases, constructors, and `str`
conversions from being used by fixes, including in module-level code.
Sources using dynamic code, namespace dictionaries, or dynamic import/attribute
builtins are left unchanged. This includes `exec`, `eval`, `globals`, `locals`,
`vars`, `__import__`, `getattr`, `setattr`, and `delattr`, exposed `builtins`,
function/frame namespaces, and module registries (`sys.modules` or
`importlib.import_module`); these capabilities can overwrite aliases or generated
constructors. Bare `dir()` use retains imports while supported calls remain eligible.
Fixes also retain imports referenced by annotations or `__all__`, and avoid
constructors affected by Pathlib mutations. Sources containing wildcard imports
are left unchanged because
their exports can overwrite names used by a fix. Sources using type parameters
or type aliases are also left unchanged because their annotation scopes and lazy
values are not analyzed by the fixer. `abspath`, `splitext`, `sep`, and `pathsep`
are not rewritten because their former replacements changed behavior
or returned incorrect values. `exists`, `isfile`, and `isdir` are also left
unchanged: on supported Python versions, their Pathlib replacements can raise
filesystem errors where `os.path` returns `False`.

**Review every diff and test the result.** Pathlib normalizes path strings, so
conversions involving variable values can still affect string-sensitive code.
The fixer does not promise to remove every violation. Diff colors are enabled
only on interactive terminals.

## Pre-commit hook integration

Add to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/pszemraj/pathlint
    rev: v0.3.0  # Use the release you want to pin
    hooks:
      - id: pathlint
        args: [--exclude, vendor, --exclude, backups]
```

Install and verify the hook:

```bash
pre-commit install
pre-commit run pathlint --all-files
```

The `pathlint` hook runs `pathlint check`. To apply experimental fixes instead,
use the `pathlint-fix` hook, which runs `pathlint fix`. Review its changes before
committing.

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
```

## License

MIT License — see [LICENSE.txt](LICENSE.txt).
