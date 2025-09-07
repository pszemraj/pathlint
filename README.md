# pathlint

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://pypi.org/project/pathlint/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![PyPI version](https://badge.fury.io/py/pathlint.svg)](https://pypi.org/project/pathlint/)

> Fast linter to detect os.path usage and encourage pathlib adoption

## Why pathlint?

Still using `os.path` in 2025+? `pathlint` is a fast, comprehensive linter that detects **all** `os.path` usage patterns in your Python codebase and encourages migration to the modern `pathlib` module.

### Key Features

- **Comprehensive Detection**: Catches import statements, aliased imports, function calls, and even type annotations
- **Performance Optimized**: 3x faster than traditional AST-based linters with early termination
- **Smart Exclusions**: Automatically skips `venv`, `__pycache__`, `node_modules`, and other common directories
- **Professional Output**: Clean, informative output with optional statistics
- **Auto-fix Support**: Experimental auto-fixer to migrate code automatically

## Installation

```bash
pip install pathlint
```

## Usage

### Basic Linting

```bash
# Lint files or directories
pathlint myfile.py
pathlint src/
pathlint .

# Multiple paths
pathlint src/ tests/ scripts/
```

### Advanced Options

```bash
# Show statistics about worst offenders
pathlint . --stats

# Aggressive mode (for fun)
pathlint . --aggressive

# Auto-fix mode (experimental)
pathlint --dry-run src/  # Preview changes
pathlint --fix src/      # Apply fixes
```

## What It Detects

pathlint catches ALL these patterns:

```python
# Import patterns
import os.path
import os.path as ospath
from os import path
from os import path as p
from os.path import join, exists

# Direct usage
os.path.exists('file.txt')
os.path.join('dir', 'file')
path.dirname(__file__)  # After 'from os import path'

# Type annotations (missed by most linters!)
def process(f: os.path.PathLike):
    pass

def get_path() -> os.path.PathLike:
    return 'test'
```

## Output Format

### Clean Files
```
✓ 42 files checked - no os.path usage found!
```

### Files with Issues
```
/path/to/file.py
  L   1: import os.path
  L  23: x = os.path.join('a', 'b')
  L  45: def process(f: os.path.PathLike):

────────────────────────────────────────
Files checked:     42
Files with issues: 3
Total violations:  7

✗ Found os.path usage. Migrate to pathlib.
```

### With Statistics (`--stats`)
```
Worst offenders:
   12 - legacy_utils.py
    5 - old_config.py
    2 - setup.py
```

## Exit Codes

- `0` - No os.path usage found
- `1` - os.path usage detected
- `2` - Error (no files found, invalid paths, etc.)

## Performance

Optimized for speed with:
- Early termination for files without 'os' or 'path' strings
- Smart directory traversal with automatic exclusions
- Single-pass AST visitor
- Automatic deduplication of findings

Benchmarks on real codebases:
```
100 files: 0.31s (vs 0.84s traditional)
500 files: 1.1s (vs 4.2s traditional)
```

## Auto-fix (Experimental)

Pathlint can automatically migrate common os.path patterns:

```bash
# Preview changes without modifying files
pathlint --dry-run myfile.py

# Apply fixes (modifies files!)
pathlint --fix myfile.py

# Fix entire directory
pathlint --fix src/
```

Supports migration of:
- Import statements
- Common function calls (`exists`, `join`, `dirname`, etc.)
- Path attributes
- Automatic `pathlib` import addition

**⚠️ Warning**: Always review auto-fixed code and test thoroughly!

## Development

```bash
# Install with dev dependencies
pip install -e .[dev,test]

# Run tests
pytest

# Format code
ruff format .

# Check linting
ruff check --fix .
```

## Why Pathlib?

`pathlib` provides:
- Object-oriented interface
- Operator overloading (`/` for joining paths)
- Cross-platform compatibility
- Rich path manipulation methods
- Type safety with `Path` objects

```python
# Old way (os.path)
import os.path
filepath = os.path.join(os.path.dirname(__file__), 'data', 'config.json')
if os.path.exists(filepath):
    abs_path = os.path.abspath(filepath)

# Modern way (pathlib)
from pathlib import Path
filepath = Path(__file__).parent / 'data' / 'config.json'
if filepath.exists():
    abs_path = filepath.resolve()
```

**Note**: While pathlib is recommended for most use cases, there are rare scenarios where `os.path` might offer better performance[^1].

[^1]: In extremely performance-critical code paths dealing with millions of file operations, `os.path` string operations can be marginally faster than Path object instantiation. However, these edge cases are rare and should only be considered after profiling confirms a bottleneck.

## License

MIT License - see LICENSE.txt

## Contributing

Contributions welcome! Please ensure:
1. Tests pass: `pytest`
2. Code is formatted: `ruff format .`
3. No linting errors: `ruff check .`

---

**Remember**: Friends don't let friends use `os.path` in 2025+!
