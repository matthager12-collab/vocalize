"""What `vocalize local install --piper` downloads, pinned.

Same security story as kokoro_manifest.py: the URL says where the voice
comes from, and the size and sha256 say which bytes are acceptable. They
were taken from a verified download (2026-09-30). Change them only
alongside a fresh, checked download.

Only an ONNX voice and its JSON config are fetched, one voice per
install. Neither is code. The engine itself (piper-tts, GPL-3.0-or-later)
is never imported by vocalize: it runs in a separate process under uv.
"""

from __future__ import annotations

from pathlib import Path

# Pinned to the commit the Hugging Face repo pointed at when the hashes
# were taken: the moving `main` branch is not an acceptable source.
REVISION = "c10ece1aade47bb51c153c893d14e5bf8e5b7117"
VOICES_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}/en/en_US"

MODEL_DIR = Path.home() / ".cache" / "vocalize" / "models" / "piper"

# Each voice has its own dataset licence, shown before download. Both are
# non-commercial; vocalize never bundles or redistributes a voice.
VOICE_LICENCES = {
    "lessac": "Blizzard 2013 licence: personal use only, no commercial use or redistribution",
    "ryan": "CC BY-NC-SA 4.0: non-commercial, share-alike",
}

FILES = [
    {
        "name": "en_US-lessac-medium.onnx",
        "url": f"{VOICES_URL}/lessac/medium/en_US-lessac-medium.onnx",
        "size": 63201294,
        "sha256": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
    },
    {
        "name": "en_US-lessac-medium.onnx.json",
        "url": f"{VOICES_URL}/lessac/medium/en_US-lessac-medium.onnx.json",
        "size": 4885,
        "sha256": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
    },
    {
        "name": "en_US-ryan-medium.onnx",
        "url": f"{VOICES_URL}/ryan/medium/en_US-ryan-medium.onnx",
        "size": 63201294,
        "sha256": "abf4c274862564ed647ba0d2c47f8ee7c9b717d27bdad9219100eb310db4047a",
    },
    {
        "name": "en_US-ryan-medium.onnx.json",
        "url": f"{VOICES_URL}/ryan/medium/en_US-ryan-medium.onnx.json",
        "size": 4883,
        "sha256": "44034c056cb15681b2ad494307c7f3f2e4499d1253c700c711fa0a4607ffe78d",
    },
]

# An allowlist, not a convenience: a voice id becomes a file name and a
# subprocess argument.
VOICES = tuple(VOICE_LICENCES)
DEFAULT_VOICE = "lessac"

# Pinned exactly, binary wheels only (`--no-build` in every uv call): an
# sdist would run a CMake build on the user's machine.
RUNTIME_PACKAGE = "piper-tts==1.8.0"
PYTHON_VERSION = "3.12"

STAMP_NAME = ".verified"
MANIFEST_VERSION = 1


def voice_files(voice: str) -> list[dict]:
    """The model and config entries for `voice`.

    Raises KeyError for anything outside VOICES, so an unchecked name can
    never reach a file path or an argument.
    """
    if voice not in VOICES:
        raise KeyError(voice)
    stem = f"en_US-{voice}-medium.onnx"
    return [entry for entry in FILES if entry["name"] in (stem, stem + ".json")]


def file_paths(voice: str, model_dir: Path | None = None) -> tuple[Path, Path]:
    """(model, config) under `model_dir`, defaulting to MODEL_DIR."""
    base = MODEL_DIR if model_dir is None else model_dir
    model, config = voice_files(voice)
    return base / model["name"], base / config["name"]


def uv_argv(voice: str, out: Path, length_scale: float = 1.0, model_dir: Path | None = None) -> list[str]:
    """Everything after `uv` for one synthesis. The text goes to stdin.

    --no-project is load-bearing, same as Kokoro's: without it `uv run`
    started inside a project directory would adopt that project.
    """
    model, config = file_paths(voice, model_dir)
    return [
        "run", "--no-project", "--no-build",
        "--python", PYTHON_VERSION,
        "--with", RUNTIME_PACKAGE,
        "python", "-m", "piper",
        "-m", str(model), "-c", str(config),
        "-f", str(out),
        "--length-scale", f"{length_scale:.3f}",
    ]


def selftest_argv(model_dir: Path | None = None, voice: str = DEFAULT_VOICE) -> list[str]:
    """What `local install` runs to warm the runtime: `--help` loads the
    engine without needing text or a voice file."""
    return [
        "run", "--no-project", "--no-build",
        "--python", PYTHON_VERSION,
        "--with", RUNTIME_PACKAGE,
        "python", "-m", "piper", "--help",
    ]
