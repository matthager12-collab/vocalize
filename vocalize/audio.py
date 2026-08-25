"""Save audio to disk and play it through whatever the OS has on hand.

Deliberately avoids pulling in a heavy playback dependency (pydub /
simpleaudio / ffmpeg-python) — this just shells out to a system
player that's virtually always already installed, and fails with a
clear message if none is found.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from .exceptions import NoAudioPlayerError

_CANDIDATES = {
    "Darwin": [["afplay"]],
    "Linux": [["mpg123"], ["ffplay", "-nodisp", "-autoexit"], ["cvlc", "--play-and-exit"]],
    "Windows": [["powershell", "-c"]],  # special-cased below
}


def save(audio: bytes, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)
    return path


def play(path: Path) -> None:
    system = platform.system()

    if system == "Windows":
        # PowerShell's SoundPlayer only handles WAV; MediaPlayer via
        # System.Media works for more formats through Windows Media.
        cmd = [
            "powershell",
            "-c",
            f"(New-Object Media.SoundPlayer '{path}').PlaySync();",
        ]
        subprocess.run(cmd, check=True)
        return

    for candidate in _CANDIDATES.get(system, []):
        exe = candidate[0]
        if shutil.which(exe):
            subprocess.run([*candidate, str(path)], check=True)
            return

    raise NoAudioPlayerError(
        f"No supported audio player found for {system}. "
        f"Install one of: {', '.join(c[0] for c in _CANDIDATES.get(system, []))} "
        f"— or open the saved file manually: {path}"
    )
