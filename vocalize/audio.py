"""Save audio to disk and play it through whatever the OS has on hand.

Deliberately avoids pulling in a heavy playback dependency (pydub /
simpleaudio / ffmpeg-python) — this just shells out to a system
player that's virtually always already installed, and fails with a
clear message if none is found.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
from pathlib import Path

from .exceptions import AudioPlaybackError, NoAudioPlayerError

_CANDIDATES = {
    "Darwin": [["afplay"]],
    "Linux": [["mpg123"], ["ffplay", "-nodisp", "-autoexit"], ["cvlc", "--play-and-exit"]],
}

# Same directory as tts.DEFAULT_CACHE_DIR — spelled out here so audio has
# no reason to import the TTS layer.
_PID_FILE = Path.home() / ".cache" / "vocalize" / "play.pid"

# Every executable play() can launch. `vocalize stop` refuses to kill a PID
# whose process isn't one of these — the recorded PID may have been reused
# by something else after the player exited uncleanly.
_PLAYER_NAMES = {"afplay", "mpg123", "ffplay", "cvlc", "powershell"}


def _proc_start_time(pid: int) -> str:
    """The process's launch timestamp per ps, or "" if it can't be read.

    (pid, start time) together identify a process for practical purposes —
    a recycled PID gets a new start time, so a stale record never matches.
    """
    try:
        return subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
    except OSError:
        return ""


def _clear_own_record(pid: int) -> None:
    """Remove the PID file only if it still holds this play's own record.

    When plays overlap, the later one overwrote the file; the earlier
    play's exit must not destroy the record of a player that is still
    running.
    """
    try:
        if _PID_FILE.read_text().splitlines()[:1] == [str(pid)]:
            _PID_FILE.unlink()
    except OSError:
        pass


def _run_tracked(cmd: list[str]) -> None:
    """Run the player with its identity on disk, so `vocalize stop` can
    kill it: PID on line one, ps launch timestamp on line two.

    A SIGTERM exit is treated as success: that is `vocalize stop` doing its
    job, not the player failing.
    """
    proc = subprocess.Popen(cmd)
    try:
        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PID_FILE.write_text(f"{proc.pid}\n{_proc_start_time(proc.pid)}\n")
    except OSError:
        pass  # tracking is best-effort; playback itself still works
    try:
        returncode = proc.wait()
    finally:
        _clear_own_record(proc.pid)
    if returncode not in (0, -signal.SIGTERM):
        raise subprocess.CalledProcessError(returncode, cmd)


def _is_known_player(pid: int) -> bool:
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
    except OSError:
        return False
    return Path(out).name in _PLAYER_NAMES if out else False


def stop_playback() -> bool:
    """Kill the player a previous play() recorded. True if one was stopped.

    Works across processes: the /speak hook's playback can be stopped from
    any terminal. When two plays overlap, the last one to start wins the
    PID file — stopping ends that one.

    Kills only a process that still matches the full recorded identity:
    same PID, same ps launch timestamp, and a known player name. A stale
    record (e.g. left behind when the Stop hook SIGKILLs a timed-out
    playback group) can therefore never target a recycled PID.
    """
    try:
        lines = _PID_FILE.read_text().splitlines()
        pid = int(lines[0])
        recorded_start = lines[1] if len(lines) > 1 else ""
    except (OSError, ValueError, IndexError):
        return False
    if (
        not recorded_start
        or _proc_start_time(pid) != recorded_start
        or not _is_known_player(pid)
    ):
        _PID_FILE.unlink(missing_ok=True)  # stale, reused, or unverifiable — never kill
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        _PID_FILE.unlink(missing_ok=True)
        return False
    _PID_FILE.unlink(missing_ok=True)
    return True


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
            _run_tracked(cmd)
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
                _run_tracked([*candidate, str(path)])
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
