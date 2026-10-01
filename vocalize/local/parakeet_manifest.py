"""What `vocalize local install --stt` downloads for the Parakeet engine, pinned.

Same security story as kokoro_manifest.py and whisper_manifest.py: the URL
says where the weights come from, and the size and sha256 say which bytes
are acceptable. They were taken from a verified download (2026-09-30).
Change them only alongside a fresh, checked download.

Two files are fetched: a JSON config and one safetensors weights file.
Safetensors is a flat tensor format: nothing downloaded here is code, and
nothing is unpickled. The weights are NVIDIA's parakeet-tdt-0.6b-v2,
converted by mlx-community, licensed CC-BY-4.0 (attribution required).
"""

from __future__ import annotations

import platform
from pathlib import Path

REPO = "mlx-community/parakeet-tdt-0.6b-v2"
# Pinned to the commit the repo pointed at when the hashes were taken: the
# moving `main` branch is not an acceptable source.
REVISION = "8ae155301e23d820d82aa60d24817c900e69e487"
RELEASE_URL = f"https://huggingface.co/{REPO}/resolve/{REVISION}"

MODEL_DIR = Path.home() / ".cache" / "vocalize" / "models" / "parakeet"

FILES = [
    {
        "name": "config.json",
        "url": f"{RELEASE_URL}/config.json",
        "size": 36176,
        "sha256": "9bd323e60afe2615c983a5d9fc3a2c0470df2a03edf90c0f861bd59509d07264",
    },
    {
        "name": "model.safetensors",
        "url": f"{RELEASE_URL}/model.safetensors",
        "size": 2471559904,
        "sha256": "b958c37a6baa6874a279108755c8f2818e27bf647d72d54800a234a421341dfe",
    },
]

# Pinned exactly, binary wheels only (`--no-build` in every uv call): the
# worker runs under uv's own Python, and an unpinned `--with` would change
# the runtime under the user's feet.
RUNTIME_PACKAGE = "parakeet-mlx==0.5.2"
PYTHON_VERSION = "3.12"

STAMP_NAME = ".verified"
MANIFEST_VERSION = 1


def supported() -> bool:
    """Parakeet runs on Apple's MLX, so Apple Silicon only."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def worker_path() -> Path:
    """The standalone worker script, as an absolute path. It is only ever
    handed to uv as a file to run — vocalize never imports it."""
    return Path(__file__).resolve().parent / "parakeet_worker.py"


def runtime_argv(model_dir: Path | None = None) -> list[str]:
    """Everything after `uv` that starts the worker; callers append
    `--transcribe WAV` or `--selftest`."""
    base = MODEL_DIR if model_dir is None else model_dir
    return [
        "run", "--no-project", "--no-build",
        "--python", PYTHON_VERSION,
        "--with", RUNTIME_PACKAGE,
        str(worker_path()),
        "--model", str(base),
    ]


def selftest_argv(model_dir: Path | None = None, **_unused) -> list[str]:
    """What `local install` runs to warm the runtime: loads the model and
    transcribes half a second of generated tone (pays the one-time
    compile, so the first dictation does not)."""
    return [*runtime_argv(model_dir), "--selftest"]
