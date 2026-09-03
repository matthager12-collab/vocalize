"""Optional on-device runtimes, and the bits that install them.

Nothing in here is imported by a normal `vocalize speak`: a provider
reaches for its manifest only once a chain actually names it, and a
worker script is never imported at all — it runs under uv's own Python,
as a subprocess.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Where uv lives when it is on neither PATH nor in its own installer's
# spot (~/.local/bin): Homebrew on Apple silicon, then on Intel.
UV_FALLBACKS = (Path("/opt/homebrew/bin/uv"), Path("/usr/local/bin/uv"))


def uv_path() -> str | None:
    """uv's executable, or None. PATH first, then its default install spot.

    Shared by every on-device provider (Kokoro, Whisper): all of them run
    their worker under the same `uv run --no-project` invocation.
    """
    found = shutil.which("uv")
    if found:
        return found
    # A Services (Quick Action) environment has a bare PATH, so the usual
    # install spots are tried by name: uv's own installer, then Homebrew.
    for fallback in (Path.home() / ".local" / "bin" / "uv", *UV_FALLBACKS):
        if fallback.is_file():
            return str(fallback)
    return None
