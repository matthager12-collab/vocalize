"""What ``vocalize local install --llm`` downloads, pinned.

Same security story as whisper_manifest.py and kokoro_manifest.py: the
URL says where the weights come from, and the size and sha256 say which
bytes are acceptable.  They were taken from a verified download on the
owner's Mac — change them only alongside a fresh, checked download.

Only safetensors weights and tokenizer JSON files are fetched.  Neither
is code: nothing downloaded here is ever executed, unpickled, or eval'd.
The ``check_config`` gate runs before the stamp is written, so a
poisoned config.json can never look "installed".

DEC-027: no downloaded chat template is evaluated.  The worker builds
token ids from its own constant; ``tokenizer.apply_chat_template`` is
never called.  The manifest enforces the structural half of that
contract — refusing ``auto_map``, ``model_file``, ``custom_pipelines``,
a foreign ``tokenizer_class``, and any ``model_type`` that is not the
pinned one — so the worker never has to decide whether to trust what
it loaded.
"""

from __future__ import annotations

import json
from pathlib import Path

# The Hugging Face repo and the exact commit hash pinned from a verified
# download.  The moving branch itself ("main") is not an acceptable
# source: a later push would change what a pinned sha256 silently stops
# matching.
REPO = "mlx-community/Qwen3.5-4B-4bit"
REVISION = "0e7ffd5c629ef7719d4cbc04069232580bfa9d9c"

RELEASE_URL = f"https://huggingface.co/{REPO}/resolve/{REVISION}"

MODEL_DIR = Path.home() / ".cache" / "vocalize" / "models" / "qwen"

# Every file the install downloads.  No .py, no .jinja — nothing that
# could be evaluated.  Size and sha256 recorded from a verified download
# on 2026-09-22.
FILES = [
    {
        "name": "config.json",
        "url": f"{RELEASE_URL}/config.json",
        "size": 3366,
        "sha256": "f3efc81b2ea8d96a45301037d3ccccbcccdef44a961845c87f286aaddbc6eaaa",
    },
    {
        "name": "tokenizer.json",
        "url": f"{RELEASE_URL}/tokenizer.json",
        "size": 19989343,
        "sha256": "87a7830d63fcf43bf241c3c5242e96e62dd3fdc29224ca26fed8ea333db72de4",
    },
    {
        "name": "tokenizer_config.json",
        "url": f"{RELEASE_URL}/tokenizer_config.json",
        "size": 1139,
        "sha256": "e98f1901ac6f0adff67b1d540bfa0c36ac1a0cf59eb72ed78146ef89aafa1182",
    },
    {
        "name": "model.safetensors.index.json",
        "url": f"{RELEASE_URL}/model.safetensors.index.json",
        "size": 101944,
        "sha256": "52e534c41f7b97708329c85f762e5882bf48bd5955a422c6ae74eba321e6048a",
    },
    {
        "name": "model.safetensors",
        "url": f"{RELEASE_URL}/model.safetensors",
        "size": 3034300695,
        "sha256": "5fb9acd0246866381cf8c5c354c6db1019f6498eec4ccb4f5edcc71ffeacb2db",
    },
]

# The model type the spike confirmed (spike-notes.md § LLM).
MODEL_TYPE = "qwen3_5"

# Pinned exactly: the worker runs under uv's own Python, and an unpinned
# ``--with`` would silently change the runtime.  Recorded in the
# ``.verified`` stamp so a mismatch reports "installed by an older
# vocalize — run: vocalize local install --llm".
RUNTIME_PACKAGE = "mlx-lm==0.31.3"
PYTHON_VERSION = "3.12"

# 12 GiB: the spike measured ~1.1 GB peak RSS for the model itself, but
# the machine also needs room for the OS, the STT model, and the rest of
# vocalize.  Chosen so a 16 GB Mac runs comfortably; an 8 GB Mac would
# swap.  ``--force`` skips this gate.
MIN_RAM_BYTES = 12 * 1024**3

STAMP_NAME = ".verified"
MANIFEST_VERSION = 1

# Tokenizer classes the worker trusts.  Anything outside this list in
# tokenizer_config.json's ``tokenizer_class`` is refused by
# ``check_config`` — a foreign class could mean a custom tokenizer that
# ``trust_remote_code=True`` would be needed for, and the worker
# explicitly sets that to False.
_TOKENIZER_CLASS_ALLOWLIST = frozenset({
    "Qwen2Tokenizer",
    "Qwen2TokenizerFast",
    "PreTrainedTokenizerFast",
})

# Keys in config.json or tokenizer_config.json that must never appear:
# each one is an avenue for code execution or model swapping that the
# pinned manifest is supposed to prevent.
_REFUSED_KEYS = frozenset({"auto_map", "model_file", "custom_pipelines"})


def check_config(model_dir: Path) -> None:
    """Read config.json and tokenizer_config.json; raise on anything unsafe.

    This runs *before* the stamp is written, so a poisoned download can
    never look "installed".  Every refusal is a ``ValueError`` with a
    message naming the offending key.
    """
    config_path = model_dir / "config.json"
    tokenizer_path = model_dir / "tokenizer_config.json"

    for path in (config_path, tokenizer_path):
        if not path.is_file():
            raise ValueError(f"missing: {path.name}")

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"config.json is unreadable: {exc}") from exc

    try:
        tokenizer = json.loads(tokenizer_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"tokenizer_config.json is unreadable: {exc}") from exc

    if not isinstance(config, dict) or not isinstance(tokenizer, dict):
        raise TypeError("config.json or tokenizer_config.json is not a JSON object")

    # Refuse keys that enable code execution or model swapping.
    for blob, name in ((config, "config.json"), (tokenizer, "tokenizer_config.json")):
        for key in _REFUSED_KEYS:
            if key in blob:
                raise ValueError(f"{name} contains {key!r} — refusing to load")

    # Model type must match the pinned one.
    if config.get("model_type") != MODEL_TYPE:
        raise ValueError(
            f"config.json model_type is {config.get('model_type')!r}, "
            f"expected {MODEL_TYPE!r}"
        )

    # Tokenizer class must be in the allowlist.
    tc = tokenizer.get("tokenizer_class", "")
    if tc and tc not in _TOKENIZER_CLASS_ALLOWLIST:
        raise ValueError(
            f"tokenizer_config.json tokenizer_class {tc!r} is not in the "
            f"allowlist ({', '.join(sorted(_TOKENIZER_CLASS_ALLOWLIST))})"
        )


def worker_path() -> Path:
    """The standalone LLM worker script, as an absolute path.

    It sits next to this module and is only ever handed to uv as a file
    to run — vocalize itself never imports it.
    """
    return Path(__file__).resolve().parent / "llm_worker.py"


def selftest_argv(model_dir: Path | None = None) -> list[str]:
    """The argv ``local install --llm`` runs (after the ``uv`` executable)
    to warm the runtime.

    This is the **online** version: uv may populate its cache on the
    first run.  Every later ``_local`` call uses ``runtime_argv`` which
    carries ``--offline``.  A test asserts the two differ in exactly
    that flag.
    """
    base = MODEL_DIR if model_dir is None else model_dir
    return [
        "run", "--no-project",
        "--python", PYTHON_VERSION,
        "--with", RUNTIME_PACKAGE,
        str(worker_path()),
        "--model-dir", str(base),
        "--selftest",
    ]


def runtime_argv(model_dir: Path | None = None) -> list[str]:
    """The argv every runtime call uses — identical to ``selftest_argv``
    but with ``--offline`` and ``--once`` instead of ``--selftest``.

    ``--offline`` tells uv to refuse network access; the install selftest
    already populated the cache.
    """
    base = MODEL_DIR if model_dir is None else model_dir
    return [
        "run", "--no-project", "--offline",
        "--python", PYTHON_VERSION,
        "--with", RUNTIME_PACKAGE,
        str(worker_path()),
        "--model-dir", str(base),
        "--once",
    ]
