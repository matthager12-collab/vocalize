"""Optional on-device runtimes, and the bits that install them.

Nothing in here is imported by a normal `vocalize speak`: a provider
reaches for its manifest only once a chain actually names it, and a
worker script is never imported at all — it runs under uv's own Python,
as a subprocess.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def uv_path() -> str | None:
    """uv's executable, or None. PATH first, then its default install spot.

    Shared by every on-device provider (Kokoro, Whisper): all of them run
    their worker under the same `uv run --no-project` invocation.
    """
    found = shutil.which("uv")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "uv"
    return str(fallback) if fallback.is_file() else None
