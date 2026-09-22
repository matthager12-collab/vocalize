"""Optional on-device runtimes, and the bits that install them.

Nothing in here is imported by a normal `vocalize speak`: a provider
reaches for its manifest only once a chain actually names it, and a
worker script is never imported at all — it runs under uv's own Python,
as a subprocess.
"""

from __future__ import annotations

import os
import shutil
import subprocess
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


def physical_ram_bytes() -> int | None:
    """Total physical RAM in bytes, or None when it cannot be determined.

    Used by ``vocalize local install --llm`` to gate the download on
    machines below ``llm_manifest.MIN_RAM_BYTES``.  ``os.sysconf`` is the
    POSIX path; on macOS the constants exist under Apple's names, so both
    are tried.  The ``sysctl`` fallback covers the unlikely case where
    sysconf is compiled out.
    """
    for pages_name, size_name in (
        ("SC_PHYS_PAGES", "SC_PAGE_SIZE"),
        ("SC_PHYS_PAGES", "SC_PAGESIZE"),
        ("_SC_PHYS_PAGES", "_SC_PAGE_SIZE"),
    ):
        try:
            pages = os.sysconf(pages_name)
            size = os.sysconf(size_name)
            if pages > 0 and size > 0:
                return pages * size
        except (ValueError, OSError, AttributeError):
            continue

    # macOS fallback: sysctl hw.memsize
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass

    return None
