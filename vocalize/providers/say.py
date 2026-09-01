"""macOS `say`: the always-available last link in the chain.

No key, no network, no quota — which is exactly why it is the fallback.
Two things matter for safety here: the text goes to `say` through a file
in a 0700 temporary directory and never through argv (where every other
process on the machine could read it), and the voice name is checked
before it becomes an argument, so a config value can't smuggle in a flag.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import Settings
from ..exceptions import (
    ProviderContentError,
    ProviderTransientError,
    ProviderUnavailableError,
)

NAME = "say"
AUDIO_EXT = "m4a"
# One call, whatever the length: `say` reads a file and has no request cap.
MAX_CHARS = None
DEFAULTS = {"voice": None}

# `say`'s own voice names: letters, digits, spaces, parentheses, hyphens —
# "Bad News", "Eddy (English (UK))". A leading letter is required, which is
# what stops a voice name from ever being read as a flag.
_VOICE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ()\-]*$")

# `say -v ?` puts 2+ spaces between the name, the locale and the sample.
_VOICE_LINE_RE = re.compile(r"^(.+?)\s{2,}([A-Za-z_\-]+)\s")

# Words-per-minute `say` uses when told nothing; speed is a multiple of it.
_BASE_WPM = 175

_TIMEOUT_SECONDS = 300


def check(settings: Settings | None = None) -> None:
    if platform.system() != "Darwin" or not shutil.which("say"):
        raise ProviderUnavailableError(NAME, "only available on macOS")


def _voice(settings: Settings) -> str | None:
    voice = getattr(settings, "voice_id", None)
    if voice is None or voice == "":
        return None
    # fullmatch, not match: `$` also matches before a trailing newline, so
    # "Samantha\n" would otherwise pass and reach argv.
    if not isinstance(voice, str) or not _VOICE_RE.fullmatch(voice):
        raise ProviderContentError(
            NAME,
            f"invalid voice {voice!r} — set [providers.say] voice to a name "
            f"from `say -v ?`",
        )
    return voice


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderTransientError(NAME, "say timed out") from exc
    except OSError as exc:
        raise ProviderTransientError(NAME, f"could not run say: {exc.__class__.__name__}") from exc


def synthesize(text: str, settings: Settings) -> bytes:
    voice = _voice(settings)

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "in.txt"
        out = Path(tmp) / "out.m4a"
        source.write_text(text, encoding="utf-8")

        argv = ["say"]
        if voice:
            argv += ["-v", voice]
        if settings.speed:
            argv += ["-r", str(round(_BASE_WPM * settings.speed))]
        argv += [
            "-o", str(out),
            "--file-format=m4af",
            "--data-format=aac",
            "-f", str(source),  # the text itself never reaches argv
        ]

        result = _run(argv)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip().splitlines()
            raise ProviderTransientError(NAME, stderr[0] if stderr else "say failed")

        try:
            audio = out.read_bytes()
        except OSError as exc:
            raise ProviderTransientError(NAME, "say wrote no audio file") from exc

    if not audio:
        raise ProviderTransientError(NAME, "say produced no audio")
    return audio


def list_voices() -> list[dict]:
    result = _run(["say", "-v", "?"])
    if result.returncode != 0:
        raise ProviderTransientError(NAME, "could not list voices")

    voices = []
    for line in (result.stdout or "").splitlines():
        match = _VOICE_LINE_RE.match(line)
        if match:
            name, locale = match.group(1).strip(), match.group(2)
            voices.append({"id": name, "name": f"{name} ({locale})"})
    return voices
