"""Hash-locked dependency trees for the reviewed engines, and where they live.

Each `<engine>.txt` beside this module is a `uv pip compile --generate-hashes
--only-binary :all:` output: every package in the engine's whole tree,
pinned with `==` and one or more `--hash=sha256:`. They are data, read only
by uv; vocalize never imports anything they name.

The install step builds one environment per lock, in a directory named
after the engine and the lock file's sha256:

    uv venv <env>
    uv pip sync --python <env>/bin/python --require-hashes --only-binary :all: <lock>

`--require-hashes` refuses any package whose bytes differ from the lock or
that the lock does not hash; `--only-binary :all:` refuses to build
anything; `pip sync` installs exactly the lock, never a dependency of it.
Workers then run under that environment's own interpreter with
`uv run --no-project --offline --python <env>/bin/python` and no `--with`,
so nothing is resolved or fetched at speak time. `uv run --with-requirements`
was not used: it ignores the hashes in a requirements file (checked with a
deliberately wrong hash against a local wheel; the run still installed it).

A changed lock has a different sha256, so it names a different
environment: the old one is never trusted for it, and the engine reports
that `vocalize local install` must be run again.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

LOCKS_DIR = Path(__file__).resolve().parent

# None means "~/.cache/vocalize/envs", read at call time so a test's HOME
# (or a monkeypatched value) is honoured.
ENVS_DIR: Path | None = None

# Written inside an environment only after `uv pip sync` succeeded.
MARKER_NAME = ".vocalize-lock"
MARKER_VERSION = 1


def lock_file(name: str) -> Path:
    """The shipped lock called `name` (e.g. "kokoro.txt")."""
    return LOCKS_DIR / name


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def envs_dir() -> Path:
    return ENVS_DIR if ENVS_DIR is not None else Path.home() / ".cache" / "vocalize" / "envs"


def env_dir(engine: str, lock: Path) -> Path:
    """`<envs>/<engine>-<lock sha256>`: a new lock is a new environment."""
    try:
        digest = sha256(lock)
    except OSError:
        digest = "missing-lock"
    return envs_dir() / f"{engine}-{digest}"


def env_python(engine: str, lock: Path) -> Path:
    """The locked environment's interpreter. Not resolved: a venv's
    bin/python symlink must keep its spelling to stay that venv."""
    return env_dir(engine, lock) / "bin" / "python"


def marker_path(engine: str, lock: Path) -> Path:
    return env_dir(engine, lock) / MARKER_NAME


def env_ready(engine: str, lock: Path) -> bool:
    """Whether the environment for this exact lock finished installing."""
    try:
        digest = sha256(lock)
        data = json.loads(marker_path(engine, lock).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    return (
        data.get("marker_version") == MARKER_VERSION
        and data.get("engine") == engine
        and data.get("lock_sha256") == digest
        and env_python(engine, lock).is_file()
    )


def write_marker(engine: str, lock: Path) -> Path:
    path = marker_path(engine, lock)
    path.write_text(
        json.dumps(
            {"marker_version": MARKER_VERSION, "engine": engine, "lock_sha256": sha256(lock)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def venv_argv(engine: str, lock: Path, python_version: str) -> list[str]:
    """Everything after `uv` that creates the (empty) environment."""
    return [
        "venv", "--clear", "--no-project", "--python", python_version,
        str(env_dir(engine, lock)),
    ]


def sync_argv(engine: str, lock: Path) -> list[str]:
    """Everything after `uv` that fills it with exactly the lock, by hash."""
    return [
        "pip", "sync", "--python", str(env_python(engine, lock)),
        "--require-hashes", "--only-binary", ":all:",
        str(lock),
    ]


def identity(lock: Path) -> str:
    """What a warm server's fingerprint carries for a locked runtime."""
    try:
        return f"lock-sha256:{sha256(lock)}"
    except OSError:
        return "lock-sha256:missing"
