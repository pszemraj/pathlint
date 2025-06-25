#!/usr/bin/env python3
"""Example of mixed code - some pathlib, some os.path."""

import os
from pathlib import Path

# Good: using pathlib
def read_config(config_name):
    config_path = Path.home() / ".config" / config_name
    if config_path.exists():
        return config_path.read_text()
    return None

# Bad: still using os.path
def backup_file(filepath):
    backup_name = os.path.splitext(filepath)[0] + ".bak"
    if os.path.exists(filepath):
        import shutil
        shutil.copy2(filepath, backup_name)
        return backup_name
    return None

# Mixed usage in the same function
def process_directory(dir_path):
    # Good: using Path object
    directory = Path(dir_path)
    
    # Bad: falling back to os.path
    for entry in os.listdir(dir_path):
        full_path = os.path.join(dir_path, entry)
        print(f"Processing: {full_path}")
    
    # Good: using pathlib methods
    python_files = list(directory.glob("*.py"))
    return python_files

# Importing just for one use - still bad!
from os.path import expanduser
home = expanduser("~")