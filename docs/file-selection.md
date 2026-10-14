# File selection

`check`, `fix`, and `files` share the same selection rules. Use `files` to
inspect the selected Python files without parsing or modifying them:

```bash
pathlint files .
pathlint check . --exclude vendor --exclude 'training/wandb/*' --exclude backups
pathlint files . -e '*.generated.py' -e 'src/legacy/'
pathlint fix . --exclude vendor --dry-run
```

## Selection precedence

1. Explicit `--exclude` patterns apply to every input, including named files
   and filenames supplied by pre-commit.
2. Explicitly named Python files bypass Git and built-in exclusions.
3. Directory scans apply built-in exclusions and, when Git is available and
   enabled, select tracked files and untracked files that Git does not ignore.

File symlinks select their resolved Python targets once; exclusions match both
the link and target paths.

## Exclusion patterns

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

Disable Git ignores or built-in exclusions independently:

```bash
pathlint check . --no-gitignore
pathlint check . --no-default-excludes
```

## Git rules

In Git worktrees, directory scans include tracked files and untracked files
that Git does not ignore. Git handles nested `.gitignore` rules, negation,
repository-local excludes, and global ignores. Tracked files remain eligible
even when they match an ignore rule. Nonexcluded nested repositories use their
own Git rules.

Git is optional. Without Git, directory scans use built-in and explicit
exclusions; `.gitignore` rules are not interpreted.

Return to the [README](../README.md).
