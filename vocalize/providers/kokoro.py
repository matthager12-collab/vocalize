"""Kokoro: speech that never leaves the machine, once you opt in.

Opt-in is the whole shape of this module. `pip install vocalize-cli`
brings none of it: no onnxruntime, no numpy, no model weights, and
nothing here imports them either. The runtime lives in uv's cache under
its own Python 3.12, the weights live in ~/.cache/vocalize/models, and
both arrive only when the user runs `vocalize local install`.

Until then `check()` fails with the command to run, and the chain treats
that as "try the next provider" — the same as a missing API key.

The model takes ~0.8 s to load and 326 MB of RAM, so the worker is
resident: one subprocess per (voice, speed, language), reused for every
chunk of a read, torn down at exit. Text reaches it as a JSON line on
stdin, never as an argument.
"""

from __future__ import annotations

import atexit
import json
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from ..config import Settings
from ..exceptions import (
    ProviderContentError,
    ProviderTransientError,
    ProviderUnavailableError,
)
from ..local import install as install_module
from ..local import kokoro_manifest as manifest

NAME = "kokoro"
AUDIO_EXT = "wav"
# The worker splits any length itself; this cap exists so streaming has
# pieces to play — ~400 characters is 20-25 seconds of speech.
MAX_CHARS = 400
STREAMING = True
DEFAULTS = {"voice": manifest.DEFAULT_VOICE, "language": manifest.DEFAULT_LANGUAGE}

# The one place a process is created. Tests replace it with a fake that
# speaks the JSON-line protocol, so nothing real is ever spawned.
SESSION_SEAM = subprocess.Popen

_UV_DOCS = "https://docs.astral.sh/uv/"
_INSTALL_HINT = "run: vocalize local install"

# 400 characters take about 5 seconds to render on an M3. The timeout is
# for a wedged worker, not a slow one.
_REQUEST_TIMEOUT = 300


def uv_path() -> str | None:
    """uv's executable, or None. PATH first, then its default install spot."""
    found = shutil.which("uv")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "uv"
    return str(fallback) if fallback.is_file() else None


def installed(model_dir: Path | None = None) -> tuple[bool, str]:
    """(ready, reason). Cheap enough to call on every synthesize.

    Both files must exist at the manifest's exact size, and the stamp
    left behind by a verified install must match the manifest's version
    and hashes. Sizes are checked, hashes are not re-computed — hashing
    326 MB per run would cost more than the synthesis.
    """
    base = manifest.MODEL_DIR if model_dir is None else model_dir

    for entry in manifest.FILES:
        path = base / entry["name"]
        try:
            size = path.stat().st_size
        except OSError:
            return False, f"not installed — {_INSTALL_HINT}"
        if size != entry["size"]:
            return False, (
                f"{entry['name']} is the wrong size — reinstall with: "
                f"vocalize local install"
            )

    stamp = install_module.read_stamp(base)
    if stamp is None:
        return False, f"not verified — {_INSTALL_HINT}"
    if stamp.get("manifest_version") != manifest.MANIFEST_VERSION:
        return False, f"installed by an older vocalize — {_INSTALL_HINT}"

    recorded = stamp.get("files") or {}
    for entry in manifest.FILES:
        seen = recorded.get(entry["name"]) or {}
        if (seen.get("sha256"), seen.get("size")) != (entry["sha256"], entry["size"]):
            return False, f"{entry['name']} does not match this release — {_INSTALL_HINT}"

    return True, ""


def _voice(settings: Settings | None) -> str:
    """The voice id, checked against the manifest before it can reach argv."""
    voice = getattr(settings, "voice_id", None) or manifest.DEFAULT_VOICE
    if voice not in manifest.VOICES:
        raise ProviderContentError(
            NAME,
            f"unknown voice {voice!r} — set [providers.kokoro] voice to one of "
            f"the {len(manifest.VOICES)} ids from `vocalize voices --provider kokoro`",
        )
    return voice


def check(settings: Settings | None = None) -> None:
    if uv_path() is None:
        raise ProviderUnavailableError(
            NAME,
            f"uv is not installed — see {_UV_DOCS} then run: vocalize local install",
        )

    ready, reason = installed()
    if not ready:
        raise ProviderUnavailableError(NAME, reason)

    _voice(settings)


class _Session:
    """One resident worker: the model loads once, not once per chunk."""

    def __init__(self, key: tuple, proc) -> None:
        self.key = key
        self.proc = proc
        self._next_id = 0
        self._replies: queue.Queue = queue.Queue()
        # A reader thread rather than select(): stdout is a text-mode pipe,
        # where select on the fd and the buffer disagree.
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:
                self._replies.put(line)
        except (OSError, ValueError):
            pass
        self._replies.put(None)  # EOF: the worker is gone

    def request(self, payload: dict) -> dict:
        self._next_id += 1
        payload = {"id": self._next_id, **payload}
        try:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ProviderTransientError(NAME, "the worker stopped accepting text") from exc

        try:
            line = self._replies.get(timeout=_REQUEST_TIMEOUT)
        except queue.Empty:
            raise ProviderTransientError(
                NAME, f"the worker did not answer within {_REQUEST_TIMEOUT}s"
            ) from None

        if line is None:
            raise ProviderTransientError(NAME, "the worker exited")

        try:
            reply = json.loads(line)
        except ValueError:
            raise ProviderTransientError(NAME, "the worker sent a malformed reply") from None
        if not isinstance(reply, dict):
            raise ProviderTransientError(NAME, "the worker sent a malformed reply")
        if reply.get("id") != payload["id"]:
            # One stray line on stdout would otherwise pair every later
            # reply with the wrong request, forever. The worker goes.
            self.close()
            raise ProviderTransientError(NAME, "the worker replied out of order")
        return reply

    def close(self) -> None:
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except (OSError, ValueError, AttributeError):
                pass
        try:
            self.proc.terminate()
        except (OSError, ValueError, AttributeError):
            pass


_session: _Session | None = None


def _argv(voice: str, speed: float, language: str, uv: str) -> list[str]:
    model, voices = manifest.file_paths()
    # --no-project is load-bearing: without it, `uv run` started from a
    # directory that holds a pyproject.toml (this repo, say) treats it as a
    # uv project and REPLACES its .venv with a uv-managed one. The worker
    # must only ever get the ephemeral --with environment.
    return [
        uv, "run", "--no-project",
        "--python", manifest.PYTHON_VERSION,
        "--with", manifest.RUNTIME_PACKAGE,
        str(manifest.worker_path()),
        "--model", str(model),
        "--voices", str(voices),
        "--voice", voice,
        "--speed", str(speed),
        "--lang", language,
        "--serve",
    ]


def _session_for(voice: str, speed: float, language: str) -> _Session:
    """The resident worker for these settings, started on first use.

    A different voice, speed or language needs a different worker, so the
    old one is retired rather than reconfigured.
    """
    global _session

    key = (voice, speed, language)
    if _session is not None and _session.key == key:
        return _session
    if _session is not None:
        _session.close()
        _session = None

    uv = uv_path()
    if uv is None:
        raise ProviderUnavailableError(
            NAME, f"uv is not installed — see {_UV_DOCS} then run: vocalize local install"
        )

    try:
        # stderr is left attached to the terminal: uv's first-run progress
        # is worth seeing, and an unread stderr pipe can wedge the worker.
        proc = SESSION_SEAM(
            _argv(voice, speed, language, uv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=tempfile.gettempdir(),  # never the caller's project dir
        )
    except OSError as exc:
        raise ProviderTransientError(NAME, f"could not start the worker: {exc}") from exc

    _session = _Session(key, proc)
    return _session


def close() -> None:
    """Terminate the resident worker, if there is one."""
    global _session
    if _session is not None:
        _session.close()
        _session = None


atexit.register(close)


def synthesize(text: str, settings: Settings) -> bytes:
    voice = _voice(settings)
    speed = settings.speed if settings.speed else 1.0
    language = getattr(settings, "language", None) or manifest.DEFAULT_LANGUAGE

    session = _session_for(voice, speed, language)

    # 0700 by default, and the only thing written into it is audio.
    with tempfile.TemporaryDirectory(prefix="vocalize-kokoro-") as tmp:
        out = Path(tmp) / "piece.wav"
        try:
            reply = session.request({"text": text, "out": str(out)})
        except ProviderTransientError:
            close()  # a broken worker is never reused
            raise

        if not reply.get("ok"):
            error = str(reply.get("error") or "synthesis failed")
            raise ProviderTransientError(NAME, error)

        try:
            audio = out.read_bytes()
        except OSError as exc:
            raise ProviderTransientError(NAME, "the worker wrote no audio") from exc

    if not audio:
        raise ProviderTransientError(NAME, "the worker produced no audio")
    return audio


def list_voices() -> list[dict]:
    """The manifest's ids. Static on purpose: no model load to list names."""
    return [{"id": voice, "name": voice} for voice in manifest.VOICES]
