"""Optional, explicitly selected pre-existing runtimes; never provision them."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

IDENTITY_ENV = "_VOCALIZE_RUNTIME_IDENTITY"
_ENGINES = {
    "mlx-lm==0.31.3": ("llm", "VOCALIZE_LLM_PYTHON"),
    "pywhispercpp==1.5.1": ("whisper", "VOCALIZE_WHISPER_PYTHON"),
}


class RuntimeSelectionError(ValueError):
    """An explicit runtime is unusable; the caller must not fall back to uv."""


@dataclass(frozen=True)
class Selection:
    engine: str
    python: str
    identity: str


def selection(package: str) -> Selection | None:
    """Preserve venv executable spelling: resolving its symlink loses the venv."""
    engine, variable = _ENGINES[package]
    if variable not in os.environ:
        return None
    value = os.environ[variable]
    path = Path(value)
    try:
        if not value or "\x00" in value or not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeSelectionError(f"{variable} must name an existing absolute Python executable")
        info = path.stat()
        # Identity distinguishes explicit environments even when bin/python
        # symlinks in several venvs share the same underlying interpreter.
        identity = hashlib.sha256(json.dumps([
            package, value, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            hashlib.sha256(Path(__file__).with_name("runtime_worker.py").read_bytes()).hexdigest(),
        ]).encode()).hexdigest()
    except (OSError, ValueError) as exc:
        if isinstance(exc, RuntimeSelectionError):
            raise
        raise RuntimeSelectionError(f"{variable} is not an accessible Python executable") from None
    return Selection(engine, value, identity)


def environment(selected: Selection | None = None) -> dict[str, str]:
    """One allowlist for cold and warm workers; never inherit credentials/hooks."""
    env = {name: os.environ[name] for name in (
        "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT",
        "UV_CACHE_DIR", "XDG_CACHE_HOME",
    ) if name in os.environ}
    # uv's own spellings of --offline and --no-python-downloads: a command
    # line that ever lost its flags would still not reach the network.
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    if selected is not None:
        env[IDENTITY_ENV] = selected.identity
    return env


# What uv prints when --offline or --no-python-downloads stopped it: the
# packages or the pinned Python are not in its cache. Only matched, never
# shown, because worker diagnostics can quote private input.
_OFFLINE_MARKERS = (
    "offline", "network connectivity is disabled", "network was disabled",
    "python downloads are set to 'never'", "no interpreter found",
)


def offline_failure(stderr) -> bool:
    """True when a worker's diagnostics say uv could not start it offline."""
    text = (stderr or "").lower() if isinstance(stderr, str) else ""
    return any(marker in text for marker in _OFFLINE_MARKERS)


def reinstall_hint(install_command: str = "vocalize local install") -> str:
    """The one way out of an uncached runtime; nothing retries online."""
    return f"its runtime is not cached for offline use; run `{install_command}` again"


def command(manifest, legacy_argv, selected: Selection | None) -> list[str]:
    """Use uv only when no explicit runtime was requested.

    The legacy route remains compatible; offline uv may still populate its
    environment cache. Only explicit selection promises no provisioning.
    """
    if selected is None:
        if not legacy_argv or not legacy_argv[0]:
            raise RuntimeSelectionError("uv is unavailable and no explicit runtime was selected")
        return list(legacy_argv)
    worker = str(manifest.worker_path())
    offset = legacy_argv.index(worker)
    return [selected.python, "-I", "-B", str(Path(__file__).with_name("runtime_worker.py")),
            selected.engine, *legacy_argv[offset + 1:]]
