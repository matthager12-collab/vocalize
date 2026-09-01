"""What `vocalize local install` downloads, pinned.

Every value here is load-bearing for the security story: the URL says
where the weights come from, and the size and sha256 say which bytes are
acceptable. They were taken from a verified download during the spike
(2026-09-01) — change them only alongside a fresh, checked download.

Only ONNX weights and a numpy voice pack are fetched. Neither is code:
nothing downloaded here is ever executed or unpickled.
"""

from __future__ import annotations

from pathlib import Path

# The GitHub release the two files are pinned to.
RELEASE_TAG = "model-files-v1.1"
RELEASE_URL = (
    f"https://github.com/thewh1teagle/kokoro-onnx/releases/download/{RELEASE_TAG}"
)

MODEL_DIR = Path.home() / ".cache" / "vocalize" / "models" / "kokoro"

FILES = [
    {
        "name": "kokoro-v1.0.onnx",
        "url": f"{RELEASE_URL}/kokoro-v1.0.onnx",
        "size": 325505369,
        "sha256": "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
    },
    {
        "name": "voices-v1.0.bin",
        "url": f"{RELEASE_URL}/voices-v1.0.bin",
        "size": 28214398,
        "sha256": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    },
]

MODEL_FILE = FILES[0]["name"]
VOICES_FILE = FILES[1]["name"]

# Pinned exactly: the worker runs under uv's own Python, and an unpinned
# `--with` would silently change the runtime under the user's feet.
RUNTIME_PACKAGE = "kokoro-onnx==0.6.1"
# kokoro-onnx pins <3.14, so it cannot run in vocalize's own 3.14 venv.
PYTHON_VERSION = "3.12"

# Written only after both files verify. Its presence is what `check()`
# trusts, so a half-finished install never looks installed.
STAMP_NAME = ".verified"
MANIFEST_VERSION = 1

SAMPLE_RATE = 24000

# The 54 ids in the v1.0 voice pack. This list is an allowlist, not a
# convenience: a voice id becomes a subprocess argument.
VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
    "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
    "ef_dora", "em_alex", "em_santa",
    "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
    "if_sara", "im_nicola",
    "pf_dora", "pm_alex", "pm_santa",
)

DEFAULT_VOICE = "af_heart"
DEFAULT_LANGUAGE = "en-us"


def worker_path() -> Path:
    """The standalone worker script, as an absolute path.

    It sits next to this module and is only ever handed to uv as a file
    to run — vocalize itself never imports it.
    """
    return Path(__file__).resolve().parent / "kokoro_worker.py"


def file_paths(model_dir: Path | None = None) -> tuple[Path, Path]:
    """(model, voices) under `model_dir`, defaulting to MODEL_DIR."""
    base = MODEL_DIR if model_dir is None else model_dir
    return base / MODEL_FILE, base / VOICES_FILE
