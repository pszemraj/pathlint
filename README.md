# pathlint

[![CI](https://github.com/pszemraj/pathlint/actions/workflows/ci.yml/badge.svg)](https://github.com/pszemraj/pathlint/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/pypi/pyversions/pathlint.svg)](https://pypi.org/project/pathlint/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

> An opinionated command-line linter for Python that aggressively encourages the use of `pathlib` over `os.path`.

## 🔥 Why pathlint?

Still using `os.path` in 2024? It's time to modernize your code! `pathlint` will find every instance of legacy `os.path` usage in your codebase and push you to adopt Python's modern `pathlib` module.

### The Aggressive Approach

When `pathlint` finds `os.path` usage, it doesn't hold back:

```
!!! ARE YOU DUMB?? WHY AREN'T YOU USING PATHLIB ??? !!!
```

## 📦 Installation

```bash
pip install pathlint
```

## 🚀 Usage

### Basic Usage

Lint a single file:
```bash
pathlint myfile.py
```

Lint multiple files:
```bash
pathlint file1.py file2.py file3.py
```

Lint entire directories:
```bash
pathlint src/
```

Lint your entire project:
```bash
pathlint .
```

### Silent Mode

If the aggressive messaging is too much, use `--silent` to suppress the signature message:
```bash
pathlint --silent src/
```

## 📋 Examples

### Bad Code (will be flagged)

```python
import os.path
from os import path

# All of these will trigger pathlint
file_exists = os.path.exists("myfile.txt")
file_size = os.path.getsize("myfile.txt") 
joined = os.path.join("dir", "file.txt")
basename = os.path.basename("/path/to/file.txt")
```

### Good Code (modern pathlib)

```python
from pathlib import Path

# Modern, clean, Pythonic
file_exists = Path("myfile.txt").exists()
file_size = Path("myfile.txt").stat().st_size
joined = Path("dir") / "file.txt"
basename = Path("/path/to/file.txt").name
```

## 🔍 What pathlint Detects

- `import os.path`
- `from os import path`
- `from os.path import ...`
- Any usage of `os.path.*` functions and attributes
- `os.path.join()`, `os.path.exists()`, `os.path.dirname()`, etc.

## 🎯 Exit Codes

- **0**: No `os.path` usage found - your code is modern! 
- **1**: Found `os.path` usage - time to refactor!

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request. Make sure to:

1. Add tests for any new functionality
2. Run the test suite: `pytest`
3. Format your code: `ruff format .`
4. Check linting: `ruff check .`

## 📝 License

This project is licensed under the MIT License - see the [LICENSE.txt](LICENSE.txt) file for details.

## 🙏 Acknowledgments

Built with [PyScaffold](https://pyscaffold.org/) - a project generator for Python packages.

---

**Remember**: Friends don't let friends use `os.path` in modern Python codebases!