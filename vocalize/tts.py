"""Shared local speech cache location; providers own their synthesis paths."""
from pathlib import Path

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "vocalize"
