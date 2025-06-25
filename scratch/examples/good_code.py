#!/usr/bin/env python3
"""Example of good code that uses pathlib instead of os.path."""

from pathlib import Path

def check_file_exists(filename):
    """Good: using pathlib.exists."""
    return Path(filename).exists()

def get_file_size(filename):
    """Good: using pathlib.stat."""
    path = Path(filename)
    if path.is_file():
        return path.stat().st_size
    return 0

def join_paths(base, *parts):
    """Good: using pathlib's / operator."""
    path = Path(base)
    for part in parts:
        path = path / part
    return path

def get_directory_contents(directory):
    """Good: using pathlib.iterdir."""
    contents = []
    for item in Path(directory).iterdir():
        if item.is_dir():
            contents.append(f"[DIR] {item.name}")
        elif item.is_file():
            contents.append(f"[FILE] {item.name}")
    return contents

# Good: Getting current directory
current_dir = Path(__file__).parent.resolve()

# Good: Path manipulation
config_file = current_dir.parent / "config" / "settings.ini"

# Good: File extension handling  
def get_extension(filename):
    return Path(filename).suffix

# Good: Path splitting
def get_filename(filepath):
    return Path(filepath).name

if __name__ == "__main__":
    print(f"Current directory: {current_dir}")
    print(f"Config file: {config_file}")
    # Note: pathlib doesn't have a direct equivalent to os.path.sep
    # but you rarely need it when using pathlib properly