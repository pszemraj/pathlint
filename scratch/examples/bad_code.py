#!/usr/bin/env python3
"""Example of bad code that uses os.path instead of pathlib."""

import os
import os.path
from os import path

def check_file_exists(filename):
    """Bad: using os.path.exists."""
    return os.path.exists(filename)

def get_file_size(filename):
    """Bad: using os.path.getsize."""
    if os.path.isfile(filename):
        return os.path.getsize(filename)
    return 0

def join_paths(base, *parts):
    """Bad: using os.path.join."""
    return os.path.join(base, *parts)

def get_directory_contents(directory):
    """Bad: using os.path with os.listdir."""
    contents = []
    for item in os.listdir(directory):
        full_path = os.path.join(directory, item)
        if os.path.isdir(full_path):
            contents.append(f"[DIR] {item}")
        elif os.path.isfile(full_path):
            contents.append(f"[FILE] {item}")
    return contents

# Bad: Getting current directory
current_dir = os.path.dirname(os.path.abspath(__file__))

# Bad: Path manipulation
config_file = os.path.join(current_dir, "..", "config", "settings.ini")
config_file = os.path.normpath(config_file)

# Bad: File extension handling  
def get_extension(filename):
    return os.path.splitext(filename)[1]

# Bad: Path splitting
def get_filename(filepath):
    return os.path.basename(filepath)

if __name__ == "__main__":
    print(f"Current directory: {current_dir}")
    print(f"Config file: {config_file}")
    print(f"Path separator: {os.path.sep}")