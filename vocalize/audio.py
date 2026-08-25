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

from .exceptions import AudioPlaybackError, NoAudioPlayerError

_CANDIDATES = {
    "Darwin": [["afplay"]],
    "Linux": [["mpg123"], ["ffplay", "-nodisp", "-autoexit"], ["cvlc", "--play-and-exit"]],
}


def save(audio: bytes, path: Path) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
    except OSError as exc:
        raise AudioPlaybackError(f"Could not save audio to {path}: {exc}") from exc
    return path


def play(path: Path) -> None:
    system = platform.system()

    if system == "Windows":
        # SoundPlayer only handles WAV, so mp3 playback on Windows likely
        # fails cleanly rather than actually playing. Windows support is
        # untested — treat it as a known limitation.
        path_str = str(path).replace("'", "''")
        cmd = [
            "powershell",
            "-c",
            f"(New-Object Media.SoundPlayer '{path_str}').PlaySync();",
        ]
        try:
            subprocess.run(cmd, check=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            raise AudioPlaybackError(
                f"powershell failed to play the audio: {exc}. "
                f"The file is still saved at {path} — open it manually."
            ) from exc
        return

    for candidate in _CANDIDATES.get(system, []):
        exe = candidate[0]
        if shutil.which(exe):
            try:
                subprocess.run([*candidate, str(path)], check=True)
            except (subprocess.CalledProcessError, OSError) as exc:
                raise AudioPlaybackError(
                    f"{exe} failed to play the audio: {exc}. "
                    f"The file is still saved at {path} — open it manually."
                ) from exc
            return

    raise NoAudioPlayerError(
        f"No supported audio player found for {system}. "
        f"Install one of: {', '.join(c[0] for c in _CANDIDATES.get(system, []))} "
        f"— or open the saved file manually: {path}"
    )
