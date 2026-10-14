# CLI reference

Use `pathlint check` to detect usage, `pathlint fix` to apply experimental
migrations, and `pathlint files` to list selected files. Each command accepts
files or directories and defaults to the current directory.

```bash
pathlint --help
pathlint check --help
pathlint fix --help
pathlint files --help
python -m pathlint check .
```

See [file selection](file-selection.md) for exclusions and Git rules, and
[fixer limitations](fixer.md) before applying changes.

## Text output

```text
src/utils.py:12: PL001 os.path usage: os.path.exists(value)
Found 1 violation in 1 of 42 files checked.
```

Paths are relative to the working directory when possible, and absolute for
external files. Text output retains the stream's encoding and escapes characters
it cannot represent, including when redirected through legacy Windows encodings.
Each offending source line is reported once, even if it contains several usages.
`PL001` means "Use pathlib instead of os.path."

```bash
# Findings only; no summary
pathlint check . --quiet

# Show the five files with the most violations
pathlint check . --stats

# Optional aggressive message
pathlint check . --aggressive
```

`check --aggressive` adds `WARNING: ARE YOU KIDDING ME? USE PATHLIB!` after the
summary when violations are found. It changes only the message, not detection
or the exit code. `--quiet` suppresses the message.

Text-mode errors go to stderr.

## JSON output

```bash
pathlint check . --output-format json
pathlint check . --output-format json --stats
```

For `example.py` containing:

```python
import os

filename = os.path.join("src", "file.py")
```

`pathlint check example.py --output-format json` produces:

```json
{
  "diagnostics": [
    {
      "path": "example.py",
      "line": 3,
      "code": "PL001",
      "message": "Use pathlib instead of os.path",
      "source": "filename = os.path.join(\"src\", \"file.py\")"
    }
  ],
  "errors": [],
  "summary": {
    "files_checked": 1,
    "files_with_issues": 1,
    "violations": 1,
    "errors": 0
  }
}
```

JSON reports contain `diagnostics`, `errors`, and `summary`. Each diagnostic has
`path`, `line`, `code`, `message`, and `source`. Summary fields are
`files_checked`, `files_with_issues`, `violations`, and `errors`; files that fail
reading or parsing are not counted as checked. `--stats` adds a `statistics`
list containing up to five `{path, violations}` entries.

JSON is available only for `check` and cannot be combined with `--quiet` or
`--aggressive`. Runtime errors are included in the JSON report without mixing
text into stdout.

## Fix output

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

Diff colors are enabled only on interactive terminals.

## Exit codes

| Command | `0` | `1` | `2` |
| --- | --- | --- | --- |
| `check` | Clean or no files selected | Violations found | Input, discovery, read, or parse error |
| `fix` | Completed | - | Input, discovery, read, parse, or write error |
| `fix --dry-run` | No edits proposed | Edits proposed | Error |
| `files` | Listing completed, including empty selection | - | Input or discovery error |

Errors take precedence over violations or proposed edits. Other valid files
are still processed. Ctrl-C exits with status `130`.

## Compatibility

These legacy invocations are accepted without warnings:

```bash
pathlint .
pathlint --fix src/
pathlint --dry-run src/
```

`python -m pathlint.linter` and `python -m pathlint.autofix` are supported
module entry points. Empty selections succeed; dry runs return `1` when they
propose edits.

Return to the [README](../README.md).
