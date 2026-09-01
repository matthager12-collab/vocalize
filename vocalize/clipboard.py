"""Read the macOS clipboard.

Shells out to pbpaste rather than pulling in a cross-platform clipboard
dependency for one command — the same reasoning as audio.py's system
players.
"""

from __future__ import annotations

import platform
import subprocess

from .exceptions import ClipboardError


def read_clipboard() -> str:
    """Return the clipboard's plain-text contents, or raise ClipboardError."""
    if platform.system() != "Darwin":
        raise ClipboardError(
            "`vocalize clip` only supports macOS (it uses pbpaste). On other "
            "platforms, pipe the clipboard in yourself, e.g. "
            "`xclip -o | vocalize speak-file -`."
        )
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClipboardError(f"Could not read the clipboard: {exc}") from exc
    return result.stdout
