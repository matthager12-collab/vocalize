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
import stat
import subprocess
import threading
import time
import wave
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

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

# Where a stop that wants the read remembered names the player it is about
# to kill, for the process running that player to find (DEC-003). This is
# the *record* baton: exactly one reader consumes it, and that reader is
# the one that saves where the read stopped.
_INTERRUPT_FILE = Path.home() / ".cache" / "vocalize" / "interrupt.request"

# The *silence* order, written by every stop (DEC-013). Never consumed —
# it expires on INTERRUPT_WINDOW — because a stop has to reach every read
# already in flight, not just the one whose player was killed: the next
# read queued on the playback lock starts the moment that player dies, and
# a dictation's microphone is already opening.
_STOP_CLAIM_FILE = Path.home() / ".cache" / "vocalize" / "stop.claim"

# `ps` by absolute path, never a bare name resolved against PATH: these
# run on the dictation path too, from a Services environment whose PATH is
# not ours, and a PATH miss here reads as "unverifiable" — the player is
# then never killed and the recorder opens the microphone on a live read.
_PS = "/bin/ps"

# How long that marker is worth acting on. It covers the milliseconds
# between the stopper writing it and the player exiting — never the
# seconds a provider may then spend rendering the next piece.
INTERRUPT_WINDOW = 10.0

# The PID a stop writes when it found nothing to kill. A streamed read is
# alive between two pieces with no player running, so a stop that lands in
# that gap has no process to name — and the thread about to start the next
# piece is the one that has to obey it (DEC-012).
_NO_PLAYER = 0


class LastStop(NamedTuple):
    """What the player `vocalize stop` last killed was playing.

    `path` is the file it had open, `elapsed_seconds` how far into it the
    stop landed, and `remembered` whether the stopper asked for the read
    to be resumable. Per process, and only ever about this process's own
    players.
    """

    path: Path | None = None
    elapsed_seconds: float = 0.0
    remembered: bool = False


# Written by whichever thread ran the player — the CLI plays streamed
# pieces on a background thread — and read from the main one, so both
# sides go through the lock.
_last_stop = LastStop()
_last_stop_lock = threading.Lock()


def ensure_private_dir(path: Path) -> None:
    """Create `path` 0700, and tighten one that is already there.

    `mkdir(exist_ok=True, mode=0o700)` is a no-op on an existing
    directory, so `~/.cache/vocalize` and `~/.cache/vocalize/bin` kept the
    0755 they were first created with on every machine that had them
    before the mode was added. The files inside are 0600 either way, but
    the *listing* is the leak: it says a dictation is in progress and an
    interrupted read is being held (DEC-014).

    Lives here because this module owns four of the paths that need it;
    `dictate`, `interrupted` and `local.install` share it rather than
    each repeating the mkdir-then-chmod.
    """
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never through a symlink: `chmod` follows one, and this path is a
    # fixed, guessable name under the user's cache.
    if not path.is_symlink() and stat.S_IMODE(path.stat().st_mode) != 0o700:
        os.chmod(path, 0o700)


def last_stop() -> LastStop:
    """Where the last stopped player was, for a caller that wants to resume."""
    with _last_stop_lock:
        return _last_stop


def _record_stop(path: Path | None, elapsed: float, remembered: bool) -> None:
    global _last_stop
    with _last_stop_lock:
        _last_stop = LastStop(path, elapsed, remembered)


def _write_interrupt_request(pid: int) -> None:
    """Name the player about to be stopped, before the signal reaches it.

    Best effort and 0600: a read that cannot be resumed is no reason to
    refuse to stop it. O_NOFOLLOW because the path is guessable — a
    symlink planted there must not become a file this writes through.
    """
    try:
        ensure_private_dir(_INTERRUPT_FILE.parent)
        fd = os.open(
            _INTERRUPT_FILE,
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{pid}\n{time.time()}\n")
    except OSError:
        pass


def _read_interrupt_request() -> tuple[int, float] | None:
    """The marker's (player pid, when it was written), or None.

    `O_NOFOLLOW` for the same reason the write uses it: the path is
    guessable, and a symlink planted there would otherwise let anything
    running as the user fabricate a stop for a read nobody interrupted —
    or, pointed at nothing, make every write fail and disable the feature.
    """
    try:
        fd = os.open(_INTERRUPT_FILE, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        return int(lines[0]), float(lines[1])
    except (OSError, ValueError, IndexError):
        return None


def take_interrupt_request(pid: int) -> bool:
    """Consume a stop marker naming `pid`. True when there was a fresh one.

    Called by the thread that ran the player, in the milliseconds after it
    exits — not later, when the caller finally notices, because by then a
    slow chunk may have been rendering for a minute.

    A *fresh* marker naming another player is left exactly where it is: it
    belongs to another read, possibly in another process. One naming this
    player is removed whether or not it was still fresh, and so is any
    marker past the window — nobody is coming for it, and a stale request
    must never be picked up by a later read.
    """
    marker = _read_interrupt_request()
    if marker is None:
        return False
    wanted, written = marker
    fresh = abs(time.time() - written) < INTERRUPT_WINDOW
    if wanted != pid and fresh:
        return False
    _INTERRUPT_FILE.unlink(missing_ok=True)
    return fresh and wanted == pid


def _write_stop_claim(remembered: bool) -> None:
    """Order every read already in flight to stop (DEC-013). Best effort."""
    try:
        ensure_private_dir(_STOP_CLAIM_FILE.parent)
        fd = os.open(
            _STOP_CLAIM_FILE,
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{time.time()}\n{int(remembered)}\n")
    except OSError:
        pass


def _read_stop_claim() -> tuple[float, bool] | None:
    """(when it was written, whether it wanted the read remembered), or None."""
    try:
        fd = os.open(_STOP_CLAIM_FILE, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        return float(lines[0]), lines[1].strip() == "1"
    except (OSError, ValueError, IndexError):
        return None


def take_gap_stop(path: Path, since: float) -> bool:
    """Whether a stop this read must obey landed while nothing was playing.

    Asked before every piece of a streamed read, and before a
    non-streaming read's single file. Three states end here, and none of
    them killed a player belonging to this read:

    * the gap between two streamed pieces, which can be tens of seconds
      long while a provider renders the next chunk (DEC-012);
    * a read still inside `synthesize()`, which has no player yet;
    * a read queued on the machine-wide playback lock behind the one that
      *was* killed, which starts the moment that player dies (DEC-013).

    In all three the next thing to happen would be vocalize speaking into
    a microphone the stop was opening. The stop claim is not consumed — it
    expires — so it reaches every read in flight, while `since` (when this
    read began) keeps a read started *after* the stop from silencing
    itself.

    The record baton is separate and is consumed: only the read that takes
    `interrupt.request` saves where it stopped, so two reads silenced
    together cannot overwrite each other's record.
    """
    marker = _read_interrupt_request()
    if marker is not None and time.time() - marker[1] > INTERRUPT_WINDOW:
        _INTERRUPT_FILE.unlink(missing_ok=True)  # nobody is coming for it
        marker = None
    claim = _read_stop_claim()
    if claim is None or claim[0] < since or time.time() - claim[0] > INTERRUPT_WINDOW:
        return False
    mine = marker is not None and marker[0] == _NO_PLAYER and marker[1] >= since
    if mine:
        _INTERRUPT_FILE.unlink(missing_ok=True)
    _record_stop(path, 0.0, mine and claim[1])
    return True


def stop_found_no_player(since: float) -> bool:
    """Whether a stop made since `since` is still sitting there unclaimed.

    True means that stop found nothing playing and no player has taken its
    marker: no read was interrupted and no record is coming. False means
    one did take it — and its process may still be inside a slow chunk,
    seconds away from writing the record (DEC-012).
    """
    marker = _read_interrupt_request()
    return marker is not None and marker[0] == _NO_PLAYER and marker[1] >= since


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
    ensure_private_dir(_LOCK_FILE.parent)
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
            [_PS, "-p", str(pid), "-o", "lstart="],
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


def _run_tracked(cmd: list[str], path: Path | None = None) -> int:
    """Run the player with its identity on disk, so `vocalize stop` can
    kill it: PID on line one, ps launch timestamp on line two.

    A SIGTERM exit is treated as success: that is `vocalize stop` doing its
    job, not the player failing. The returncode is handed back so a caller
    playing a sequence can tell that stop apart from a clean finish.

    A SIGTERM also leaves `last_stop()` behind — the file, how far into it
    the stop came, and whether a stop marker for this very player was
    consumed here (DEC-003).
    """
    started = time.monotonic()
    proc = subprocess.Popen(cmd)
    try:
        ensure_private_dir(_PID_FILE.parent)
        _PID_FILE.write_text(f"{proc.pid}\n{_proc_start_time(proc.pid)}\n")
    except OSError:
        pass  # tracking is best-effort; playback itself still works
    try:
        returncode = proc.wait()
    finally:
        _clear_own_record(proc.pid)
    if returncode == -signal.SIGTERM:
        _record_stop(path, time.monotonic() - started, take_interrupt_request(proc.pid))
    if returncode not in (0, -signal.SIGTERM):
        raise subprocess.CalledProcessError(returncode, cmd)
    return returncode


def _is_known_player(pid: int) -> bool:
    try:
        out = subprocess.run(
            [_PS, "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
    except OSError:
        return False
    return Path(out).name in _PLAYER_NAMES if out else False


def _live_player_pid() -> int | None:
    """The recorded player, if it is still the process that was recorded.

    Same PID, same ps launch timestamp, and a known player name — anything
    else is stale, reused or unverifiable, and the record goes rather than
    a signal to whatever holds that PID now.
    """
    try:
        lines = _PID_FILE.read_text().splitlines()
        pid = int(lines[0])
        recorded_start = lines[1] if len(lines) > 1 else ""
    except (OSError, ValueError, IndexError):
        return None
    if (
        not recorded_start
        or _proc_start_time(pid) != recorded_start
        or not _is_known_player(pid)
    ):
        _PID_FILE.unlink(missing_ok=True)  # stale, reused, or unverifiable — never kill
        return None
    return pid


def stop_playback(*, remember: bool = False) -> bool:
    """Kill the player a previous play() recorded. True if one was stopped.

    Works across processes: the /speak hook's playback can be stopped from
    any terminal. Playback is serialized machine-wide, so there is at most
    one player at a time — but a stop reaches every read *already in
    flight*, not only that player: the next queued read starts the instant
    the lock frees, and a read still synthesizing has no player to kill
    (DEC-013). A read started after the stop is unaffected.

    Kills only a process that still matches the full recorded identity:
    same PID, same ps launch timestamp, and a known player name. A stale
    record (e.g. left behind when the Stop hook SIGKILLs a timed-out
    playback group) can therefore never target a recycled PID.

    `remember=True` (dictation, DEC-003) leaves a marker naming that player
    first, so the process running it can save where the read stopped and
    offer to continue it. A plain stop leaves nothing behind and no read is
    ever recorded.

    A remembered stop that finds *no* player still leaves a marker, naming
    none: a streamed read between two pieces is playing nothing while it
    renders the next one, and that marker is how the read learns it was
    stopped (DEC-012). The return value is unchanged — nothing was killed.
    """
    pid = _live_player_pid()
    # Before the signal, never after: the player can exit — and look for
    # these — in the microseconds after the kill lands.
    _write_stop_claim(remember)
    if remember:
        _write_interrupt_request(pid if pid is not None else _NO_PLAYER)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        if remember:
            _INTERRUPT_FILE.unlink(missing_ok=True)  # nothing was stopped
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
            return _run_tracked(cmd, path)
        except (subprocess.CalledProcessError, OSError) as exc:
            raise AudioPlaybackError(
                f"powershell failed to play the audio: {exc}. "
                f"The file is still saved at {path} — open it manually."
            ) from exc

    for candidate in _CANDIDATES.get(system, []):
        exe = candidate[0]
        if shutil.which(exe):
            try:
                return _run_tracked([*candidate, str(path)], path)
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
