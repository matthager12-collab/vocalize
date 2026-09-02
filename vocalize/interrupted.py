"""The record of a read a dictation interrupted, and the slice that resumes it.

A dictation stops whatever is playing before it opens the microphone
(DEC-003). The *playing* process is the only one that knows what was
playing — `play.pid` carries no path, and a streamed read deletes its
rendered pieces the moment it ends — so it writes the record here and
`vocalize resume` (or the dialog after a dictation) picks it up.

What is on disk, all 0600 in the user's cache and all replaced together:

  * `interrupted.<ext>` — one piece of audio: the chunk that was playing,
    or the whole file for a provider that does not stream.
  * `interrupted.txt` — the text after that piece, possibly empty.
  * `interrupted.json` — version, when it was saved, which provider spoke,
    the extension, the offset into the audio, the length of the text, and
    the voice, model, speed and chunking it was being spoken with. Those
    four are what makes the continuation the *same* read: without them it
    resumed in the config-default voice, and because the audio cache keys
    on resolved settings, every remaining chunk was a miss and was paid
    for again (DEC-014). No credential is ever in here.

`interrupted.txt` is new plaintext on disk: the audio cache next door
holds audio, not text, so this is the first time a read's words are
written out (DEC-012). Nothing new leaves the machine, but the file is
protected by its mode, by O_NOFOLLOW on the way in and out, by there
being one record at a time, and by the hour — and by nothing else. No
part of a dictation — audio or transcript — ever enters it (DEC-007).

`interrupted.json` is written last and read first: a half-written record
has no JSON and reads as no record at all. Everything in it is treated as
untrusted input, because anything running as the user can write that path.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
import wave
from pathlib import Path
from typing import NamedTuple

from . import audio as audio_module
from .auth import PROVIDER_NAMES

# Same directory as the playback lock, the ledger and the dictation
# session. Spelled out so tests can point the module at a temporary one.
CACHE_DIR = Path.home() / ".cache" / "vocalize"

# Every extension a provider can leave here (mp3, m4a, wav across the six).
# An allowlist rather than a shape check: the value becomes a file name.
_EXTS = ("mp3", "m4a", "wav")

# Past this, the read is not what the user is doing any more.
MAX_AGE = 60 * 60.0

# 2 adds voice_id/model_id/speed/chunk_chars. A version-1 record is
# discarded on upgrade like any other record this module cannot use — at
# most one read, at most an hour old.
_VERSION = 2

_AFCONVERT = "/usr/bin/afconvert"
_AFCONVERT_TIMEOUT = 60


class Record(NamedTuple):
    """A saved interrupted read, as `load()` hands it back."""

    audio_path: Path
    ext: str
    text: str
    provider: str
    offset_seconds: float
    saved_at: float
    voice_id: str | None = None
    model_id: str | None = None
    speed: float | None = None
    chunk_chars: int | None = None


# What the read was being spoken with, kept so the continuation is the
# same read rather than a new one in the config-default voice. Never a
# credential: `_run_tts`'s overrides dict also carries `api_key`, and only
# these four names are ever copied out of it.
SETTING_KEYS = ("voice_id", "model_id", "speed", "chunk_chars")

# A voice or model id becomes a provider API parameter, so it is
# shape-checked on the way out exactly as `ext` and `provider` are.
_MAX_ID_CHARS = 200
_MAX_CHUNK_CHARS = 100_000


def _checked_setting(key: str, value):
    """One stored setting, or None if it is not one this module wrote."""
    if value is None:
        return None
    if key in ("voice_id", "model_id"):
        if not isinstance(value, str) or not value or len(value) > _MAX_ID_CHARS:
            return None
        return value if value.isprintable() else None
    if key == "speed":
        from . import config

        try:
            return config.validate_speed(value, "the interrupted-read record")
        except Exception:  # noqa: BLE001 — any bad value reads as "not set"
            return None
    # chunk_chars. `bool` is an int in Python and is never a chunk size.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= _MAX_CHUNK_CHARS else None


def _json_path() -> Path:
    return CACHE_DIR / "interrupted.json"


def _text_path() -> Path:
    return CACHE_DIR / "interrupted.txt"


def _audio_path(ext: str) -> Path:
    return CACHE_DIR / f"interrupted.{ext}"


def _write_private(path: Path, data: bytes) -> None:
    """0600, truncating, never through a symlink planted at the path."""
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _read_private(path: Path) -> bytes:
    """The file itself, never what a symlink at that path points at.

    These three names are guessable, and the text goes on to be *spoken* —
    through a cloud provider, for most of the chain. A symlink planted here
    would make that a read-aloud of whatever it pointed at, so the record is
    only ever read as the plain file this module wrote.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def forget() -> None:
    """Remove the record. Called on resume, on decline, and when stale."""
    for path in (_json_path(), _text_path(), *(_audio_path(e) for e in _EXTS)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def save(
    *,
    piece: Path,
    ext: str,
    remaining_text: str,
    provider: str,
    offset_seconds: float,
    settings: dict | None = None,
) -> bool:
    """Record where a read was cut off. False if it could not be written.

    Best effort by contract: the caller is on its way out of a stopped
    read, and a cache it cannot write to is not a reason to turn that into
    an error.
    """
    if ext not in _EXTS or provider not in PROVIDER_NAMES:
        return False
    forget()  # one record at a time, whatever extension the last one used
    try:
        audio_module.ensure_private_dir(CACHE_DIR)
        _write_private(_audio_path(ext), piece.read_bytes())
        _write_private(_text_path(), remaining_text.encode("utf-8"))
        _write_private(
            _json_path(),
            json.dumps(
                {
                    "version": _VERSION,
                    "saved_at": time.time(),
                    "provider": provider,
                    "ext": ext,
                    "offset_seconds": round(max(offset_seconds, 0.0), 3),
                    "remaining_chars": len(remaining_text),
                    **{
                        key: _checked_setting(key, (settings or {}).get(key))
                        for key in SETTING_KEYS
                    },
                }
            ).encode("utf-8"),
        )
    except OSError:
        forget()
        return False
    return True


def load() -> Record | None:
    """The saved read, or None. Anything unusable is deleted on the way out.

    A record naming an extension or a provider vocalize does not have is
    not a record it wrote: it goes the same way as a stale one rather than
    reaching a file name or the provider chain.

    A record with *no* JSON at all is not unusable, it is unfinished:
    `save()` writes audio, then text, then JSON last for exactly that
    reason. `dictate._wait_for_record` polls this every 50 ms for the
    three seconds that write takes, so treating the missing file as
    corruption meant `forget()` deleting the audio and text the saver had
    just written — and the read the user asked to keep was gone (DEC-014).
    """
    try:
        raw = _read_private(_json_path())
    except FileNotFoundError:
        return None  # no record, or one still being written
    except OSError:
        forget()
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        ext = data["ext"]
        provider = data["provider"]
        offset = float(data["offset_seconds"])
        saved_at = float(data["saved_at"])
        version = data["version"]
    except (OSError, ValueError, TypeError, KeyError):
        forget()
        return None
    if (
        version != _VERSION
        or ext not in _EXTS
        or provider not in PROVIDER_NAMES
        # `json.loads` accepts the literals Infinity and NaN, and every
        # comparison against them is False: an infinite offset would pass
        # `offset < 0`, then crash `slice_from` on int(inf * framerate),
        # and an infinite `saved_at` would never expire.
        or not (math.isfinite(offset) and math.isfinite(saved_at))
        or offset < 0
        or not _audio_path(ext).is_file()
        or _audio_path(ext).is_symlink()
    ):
        forget()
        return None
    if time.time() - saved_at > MAX_AGE:
        forget()
        return None
    try:
        text = _read_private(_text_path()).decode("utf-8")
    except (OSError, ValueError):
        text = ""
    return Record(
        _audio_path(ext), ext, text, provider, offset, saved_at,
        *(_checked_setting(key, data.get(key)) for key in SETTING_KEYS),
    )


def slice_from(record: Record, workdir: Path) -> Path | None:
    """The saved audio from its offset on, as a WAV. None if nothing is left.

    `wave` can only cut a WAV, so anything else goes through `afconvert`
    first — already on every Mac, and the same tool the recorder falls back
    to. `workdir` is the caller's own 0700 temporary directory.
    """
    source = record.audio_path
    if record.ext != "wav":
        converted = workdir / "resume.wav"
        try:
            result = subprocess.run(
                [_AFCONVERT, "-f", "WAVE", "-d", "LEI16", str(source), str(converted)],
                capture_output=True, timeout=_AFCONVERT_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0 or not converted.is_file():
            return None
        source = converted

    out = workdir / "slice.wav"
    try:
        with wave.open(str(source), "rb") as reader:
            start = min(int(record.offset_seconds * reader.getframerate()),
                        reader.getnframes())
            reader.setpos(start)
            frames = reader.readframes(reader.getnframes() - start)
            if not frames:
                return None
            with wave.open(str(out), "wb") as writer:
                writer.setnchannels(reader.getnchannels())
                writer.setsampwidth(reader.getsampwidth())
                writer.setframerate(reader.getframerate())
                writer.writeframes(frames)
    except (OSError, wave.Error, EOFError):
        return None
    return out


def remember_stop(
    remaining_text: str,
    provider: str | None,
    ext: str | None,
    settings: dict | None = None,
) -> bool:
    """Save the read a dictation's stop just cut off, if that is what it was.

    The whole feature turns on this check: `audio.last_stop()` says a
    marker naming *this* process's player was consumed when it died. A
    plain `vocalize stop` writes no marker, so it records nothing.
    """
    stop = audio_module.last_stop()
    if not stop.remembered or stop.path is None or not provider or not ext:
        return False
    return save(
        piece=stop.path,
        ext=ext,
        remaining_text=remaining_text,
        provider=provider,
        offset_seconds=stop.elapsed_seconds,
        settings=settings,
    )
