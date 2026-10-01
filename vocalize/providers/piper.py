"""Piper: a small, fast voice that never leaves the machine, once you opt in.

Same opt-in shape as Kokoro. `pip install vocalize-cli` brings none of it:
the runtime is fetched by uv into its own cache and the voice is
downloaded by `vocalize local install --piper`.

Piper is GPL-3.0-or-later. It is never imported here: each piece runs
as a separate `python -m piper` process under uv, text on stdin and a WAV
file out. A cold process takes about a second, so there is no resident
worker. # ponytail: one process per piece; a resident worker if latency matters.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from ..config import Settings
from ..exceptions import (
    ProviderContentError,
    ProviderTransientError,
    ProviderUnavailableError,
)
from ..local import install as install_module
from ..local import piper_manifest as manifest
from ..local import uv_path  # re-exported, same as kokoro

NAME = "piper"
AUDIO_EXT = "wav"
# About 400 characters is 20-25 seconds of speech; the cap gives streaming
# pieces to play.
MAX_CHARS = 400
STREAMING = True
DEFAULTS = {"voice": manifest.DEFAULT_VOICE}

# The one place a process is created. Tests replace it with a fake.
RUN_SEAM = subprocess.run

_UV_DOCS = "https://docs.astral.sh/uv/"
_INSTALL_HINT = "vocalize local install --piper"
_REQUEST_TIMEOUT = 120


def _voice(settings: Settings | None) -> str:
    """The voice id, checked against the manifest before it can reach argv."""
    voice = getattr(settings, "voice_id", None) or manifest.DEFAULT_VOICE
    if voice not in manifest.VOICES:
        raise ProviderContentError(
            NAME,
            f"unknown voice {voice!r} — set [providers.piper] voice to one of: "
            f"{', '.join(manifest.VOICES)}",
        )
    return voice


def installed(voice: str = manifest.DEFAULT_VOICE, model_dir: Path | None = None) -> tuple[bool, str]:
    """(ready, reason) for `voice`. Cheap enough to call on every synthesize."""
    return install_module.installed(
        manifest, model_dir, files=manifest.voice_files(voice), install_hint=_INSTALL_HINT
    )


def check(settings: Settings | None = None) -> None:
    if uv_path() is None:
        raise ProviderUnavailableError(
            NAME, f"uv is not installed — see {_UV_DOCS} then run: {_INSTALL_HINT}"
        )
    ready, reason = installed(_voice(settings))
    if not ready:
        raise ProviderUnavailableError(NAME, reason)


def synthesize(text: str, settings: Settings) -> bytes:
    voice = _voice(settings)
    speed = settings.speed if settings.speed else 1.0
    uv = uv_path()
    if uv is None:
        raise ProviderUnavailableError(
            NAME, f"uv is not installed — see {_UV_DOCS} then run: {_INSTALL_HINT}"
        )

    # Piper reads one utterance per line: flatten so a paragraph is one read.
    line = " ".join(text.split())

    # 0700 by default, and the only thing written into it is audio.
    with tempfile.TemporaryDirectory(prefix="vocalize-piper-") as tmp:
        out = Path(tmp) / "piece.wav"
        argv = [uv, *manifest.uv_argv(voice, out, length_scale=1.0 / speed)]
        try:
            result = RUN_SEAM(
                argv, input=line + "\n", capture_output=True, text=True,
                timeout=_REQUEST_TIMEOUT, check=False,
                cwd=tempfile.gettempdir(),  # never the caller's project dir
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderTransientError(NAME, f"piper did not finish within {_REQUEST_TIMEOUT}s") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderTransientError(NAME, f"could not start piper: {exc}") from exc

        if result.returncode != 0:
            # Last stderr line only: an error must not quote the input text.
            tail = (result.stderr or "").strip().splitlines()
            raise ProviderTransientError(NAME, tail[-1][:200] if tail else "piper failed")

        try:
            audio = out.read_bytes()
        except OSError as exc:
            raise ProviderTransientError(NAME, "piper wrote no audio") from exc

    if not audio:
        raise ProviderTransientError(NAME, "piper produced no audio")
    return audio


def list_voices() -> list[dict]:
    """The manifest's ids, with each voice's licence. Static: no process."""
    return [{"id": v, "name": f"{v} ({manifest.VOICE_LICENCES[v].split(':')[0]})"} for v in manifest.VOICES]
