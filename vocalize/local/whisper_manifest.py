"""What `vocalize local install --stt` downloads, pinned.

Same security story as kokoro_manifest.py: the URL says where the weights
come from, and the size and sha256 say which bytes are acceptable. They
were taken from a verified download during the spike (2026-09-01) —
change them only alongside a fresh, checked download. See
docs/plans/2026-09-next-features/spike-2026-09-01.md.

Only ggml model files are fetched, and only one of them is ever
downloaded per install (the `--model` the user picked, default
`small.en`) — unlike Kokoro, which always needs both of its files.
Nothing downloaded here is code: pywhispercpp reads these as whisper.cpp
model weights inside the uv worker, never unpickled, never executed.
"""

from __future__ import annotations

from pathlib import Path

# Pinned to the commit whisper.cpp's `main` branch pointed at when the
# spike verified these hashes (2026-09-01) — the moving branch itself is
# not an acceptable source, because a later push to `main` would change
# what a pinned sha256 silently stops matching. Confirmed against HF's
# API (GET /api/models/ggerganov/whisper.cpp/revision/main) and a HEAD
# request on the resolve URL, whose `x-linked-size`/`x-linked-etag`
# headers match the size and sha256 below exactly.
REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
RELEASE_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{REVISION}"

MODEL_DIR = Path.home() / ".cache" / "vocalize" / "models" / "whisper"

FILES = [
    {
        "name": "ggml-base.en.bin",
        "url": f"{RELEASE_URL}/ggml-base.en.bin",
        "size": 147964211,
        "sha256": "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
    },
    {
        "name": "ggml-small.en.bin",
        "url": f"{RELEASE_URL}/ggml-small.en.bin",
        "size": 487614201,
        "sha256": "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d",
    },
    {
        "name": "ggml-large-v3-turbo-q5_0.bin",
        "url": f"{RELEASE_URL}/ggml-large-v3-turbo-q5_0.bin",
        "size": 574041195,
        "sha256": "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    },
]

# The allowlist a `--model`/`[stt] model` value is checked against before
# it can become a file name or a subprocess argument: "small.en", not
# "../etc/passwd" or "--serve".
MODELS = tuple(entry["name"][len("ggml-") : -len(".bin")] for entry in FILES)

DEFAULT_MODEL = "small.en"

# Pinned exactly, same reasoning as kokoro_manifest.RUNTIME_PACKAGE: the
# worker runs under uv's own Python, and an unpinned `--with` would
# silently change the runtime under the user's feet.
RUNTIME_PACKAGE = "pywhispercpp==1.5.1"
PYTHON_VERSION = "3.12"

# Written only after the selected file verifies. Its presence is what
# `installed()` trusts, so a half-finished install never looks done.
STAMP_NAME = ".verified"
MANIFEST_VERSION = 1

# whisper.cpp's language codes: "auto" plus its ISO 639-1 set. Not
# exhaustive of every code whisper.cpp will accept — this is an allowlist
# a config value or `--language` flag must pass before it can become a
# subprocess argument, so it lists English (the only choice for an `.en`
# model) plus the common set rather than chasing the full ~100-entry table.
LANGUAGES = (
    "auto", "en", "zh", "de", "es", "ru", "ko", "fr", "ja", "pt", "tr", "pl",
    "ca", "nl", "ar", "sv", "it", "id", "hi", "fi", "vi", "he", "uk", "el",
    "ms", "cs", "ro", "da", "hu", "ta", "no", "th", "ur", "hr", "bg", "lt",
    "la", "mi", "ml", "cy", "sk", "te", "fa", "lv", "bn", "sr", "az", "sl",
    "kn", "et", "mk", "br", "eu", "is", "hy", "ne", "mn", "bs", "kk", "sq",
    "sw", "gl", "mr", "pa", "si", "km", "sn", "yo", "so", "af", "oc", "ka",
    "be", "tg", "sd", "gu", "am", "yi", "lo", "uz", "fo", "ht", "ps", "tk",
    "nn", "mt", "sa", "lb", "my", "bo", "tl", "mg", "as", "tt", "haw", "ln",
    "ha", "ba", "jw", "su", "yue",
)


def is_english_only(model: str) -> bool:
    """Whether `model` is an `.en` variant, which whisper.cpp restricts to
    transcribing English regardless of what `--language` is asked for."""
    return model.endswith(".en")


def model_file(model: str) -> str:
    return f"ggml-{model}.bin"


def file_for(model: str) -> dict:
    """The manifest entry for `model`.

    Raises KeyError for anything not in MODELS — callers are expected to
    check the allowlist first; an unchecked model name must never reach
    this far, let alone a subprocess argument or a file path.
    """
    name = model_file(model)
    for entry in FILES:
        if entry["name"] == name:
            return entry
    raise KeyError(model)


def worker_path() -> Path:
    """The standalone worker script, as an absolute path.

    It sits next to this module and is only ever handed to uv as a file
    to run — vocalize itself never imports it.
    """
    return Path(__file__).resolve().parent / "whisper_worker.py"


def model_path(model: str, model_dir: Path | None = None) -> Path:
    """Where `model`'s file lives under `model_dir`, defaulting to MODEL_DIR.

    Routes through `file_for()` — which raises KeyError for anything off
    the MODELS allowlist — rather than building the name with
    `model_file()` directly, so a caller that skips the CLI's own
    allowlist check (`_stt_model_or_raise`) still can't turn an unchecked
    model name into a traversal path or a flag-shaped argv entry. This is
    also what `selftest_argv()` below uses to build its `--model` value.
    """
    base = MODEL_DIR if model_dir is None else model_dir
    return base / file_for(model)["name"]


def selftest_argv(model_dir: Path | None = None, *, model: str = DEFAULT_MODEL) -> list[str]:
    """The argv `local install --stt` runs (after `uv run --no-project ...`)
    to warm the runtime — this is also where the one-time Metal shader
    compile (~8s on the reference Mac) gets paid, never during a dictation.

    Takes `model=` because, unlike Kokoro, Whisper only ever has one
    model file on disk to test at a time: whichever `--model` was
    installed.
    """
    return [
        "run", "--no-project",
        "--python", PYTHON_VERSION,
        "--with", RUNTIME_PACKAGE,
        str(worker_path()),
        "--model", str(model_path(model, model_dir)),
        "--language", "en" if is_english_only(model) else "auto",
        "--selftest",
    ]
