"""Save audio to disk and play it through whatever the OS has on hand.

Deliberately avoids pulling in a heavy playback dependency (pydub /
simpleaudio / ffmpeg-python) — this just shells out to a system
player that's virtually always already installed, and fails with a
clear message if none is found.

Playback is serialized machine-wide: concurrent vocalize invocations (a
/speak issued while another read is going, two Claude Code sessions
finishing at once) queue on an exclusive file lock and come out one after
the other, never over each other.
"""

from __future__ import annotations

import io
import os
import platform
import shutil
import signal
import subprocess
import wave
from contextlib import contextmanager
from pathlib import Path

try:  # POSIX-only. Windows playback is already an untested known limitation.
    import fcntl
except ImportError:  # pragma: no cover — no Windows CI
    fcntl = None

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

# The machine-wide playback queue. Held (flock LOCK_EX) for the duration of
# one audible read; everyone else blocks here until it frees.
_LOCK_FILE = Path.home() / ".cache" / "vocalize" / "play.lock"


@contextmanager
def _playback_slot():
    """Hold the machine-wide right to be the one audible playback.

    Concurrent invocations queue on an exclusive file lock instead of
    talking over each other; waiters proceed in the order the OS grants
    the lock. Only playback is serialized — a waiter's synthesis has
    already happened by the time it gets here. The lock dies with its
    process (flock semantics), so a killed or timed-out waiter can never
    leave a stale lock behind.

    Platforms without fcntl (Windows) skip the lock: playback there is
    already documented as untested, and overlapping beats crashing.
    """
    if fcntl is None:
        yield
        return
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # blocks until the current read ends
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


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

    Defensive: with the playback slot serializing reads, records should
    never overlap — but on no-fcntl platforms (and against any future
    bypass) a later play may have overwritten the file, and the earlier
    play's exit must not destroy the record of a player still running.
    """
    try:
        if _PID_FILE.read_text().splitlines()[:1] == [str(pid)]:
            _PID_FILE.unlink()
    except OSError:
        pass


def _run_tracked(cmd: list[str]) -> int:
    """Run the player with its identity on disk, so `vocalize stop` can
    kill it: PID on line one, ps launch timestamp on line two.

    A SIGTERM exit is treated as success: that is `vocalize stop` doing its
    job, not the player failing. The returncode is handed back so a caller
    playing a sequence can tell that stop apart from a clean finish.
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
    return returncode


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
    any terminal. Playback is serialized machine-wide, so there is at most
    one player at a time; stopping it lets the next queued read (if any)
    begin — run stop again to silence that one too.

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


def play(path: Path) -> int:
    """Play one file, blocking until it ends. Returns the player's exit code.

    Queues behind any playback already running anywhere on the machine —
    two concurrent plays come out one after the other, never over each
    other. `-signal.SIGTERM` means `vocalize stop` ended it; 0 means it
    played to the end.
    """
    with _playback_slot():
        return _play_now(path)


def _play_now(path: Path) -> int:
    """The actual player launch — the caller must hold the playback slot."""
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
            return _run_tracked(cmd)
        except (subprocess.CalledProcessError, OSError) as exc:
            raise AudioPlaybackError(
                f"powershell failed to play the audio: {exc}. "
                f"The file is still saved at {path} — open it manually."
            ) from exc

    for candidate in _CANDIDATES.get(system, []):
        exe = candidate[0]
        if shutil.which(exe):
            try:
                return _run_tracked([*candidate, str(path)])
            except (subprocess.CalledProcessError, OSError) as exc:
                raise AudioPlaybackError(
                    f"{exe} failed to play the audio: {exc}. "
                    f"The file is still saved at {path} — open it manually."
                ) from exc

    raise NoAudioPlayerError(
        f"No supported audio player found for {system}. "
        f"Install one of: {', '.join(c[0] for c in _CANDIDATES.get(system, []))} "
        f"— or open the saved file manually: {path}"
    )


def play_sequence(paths, *, stop_check=None) -> bool:
    """Play each path in order. False as soon as the user stopped one.

    The whole sequence holds a single playback slot, so a read queued
    behind it starts only after the last piece — chunks of two reads never
    interleave. Each piece still goes through _play_now(), so the PID file
    always names the player that is running right now and `vocalize stop`
    keeps working unchanged. A piece killed by SIGTERM is the user stopping
    the read: the rest of the sequence is abandoned rather than played on.
    """
    with _playback_slot():
        for path in paths:
            if stop_check is not None and stop_check():
                return False
            if _play_now(path) == -signal.SIGTERM:
                return False
    return True


def stitch_wav(parts: list[bytes]) -> bytes:
    """One WAV out of several: same params, frames appended.

    Raw byte-concatenation would leave a header mid-file, so the frames
    are re-wrapped with stdlib `wave` instead.
    """
    frames: list[bytes] = []
    params = None
    for part in parts:
        try:
            with wave.open(io.BytesIO(part), "rb") as reader:
                if params is None:
                    params = reader.getparams()
                elif reader.getparams()[:3] != params[:3]:
                    raise AudioPlaybackError(
                        "Cannot join WAV pieces recorded with different "
                        "channels, sample width or frame rate."
                    )
                frames.append(reader.readframes(reader.getnframes()))
        except wave.Error as exc:
            raise AudioPlaybackError(f"Could not join WAV audio: {exc}") from exc

    if params is None:
        return b""

    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setparams(params)
        writer.writeframes(b"".join(frames))
    return out.getvalue()


def join_audio(parts: list[bytes], ext: str) -> bytes:
    """Join one provider's chunks into the single file the caller saves."""
    if len(parts) == 1:
        return parts[0]
    if ext == "wav":
        return stitch_wav(parts)
    if ext == "mp3":
        return b"".join(parts)  # frame-concatenated MP3 plays fine
    raise AudioPlaybackError(
        f"{ext} audio cannot be chunked: that provider has to answer in one "
        f"piece. Raise --chunk-chars, or pick a provider that returns MP3."
    )
