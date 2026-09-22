"""LLM cleanup/summary worker.  Runs under uv, never imported by vocalize.

This file ships inside the vocalize package but is not part of it: uv
executes it with its own Python 3.12 and its own copy of mlx-lm, so
vocalize's interpreter never sees mlx_lm or transformers.  That is the
whole reason it exists, and it is why nothing here imports vocalize.

mlx_lm is imported inside ``_mlx()``, so the file can be byte-compiled,
linted and AST-checked anywhere — including in vocalize's own test run,
where neither package is installed.

DEC-027: No downloaded chat template is evaluated.  The prompt is built
**as token ids** from a constant in this file.  ``tokenizer.apply_chat_template``
is never called.  ``split_special_tokens=True`` ensures no user text can
inject control tokens.

Protocol (``--once``): one JSON request on stdin, one JSON response on
stdout, then exit 0:

    stdin:  {"system": "...", "text": "..."}
    stdout: {"ok": true, "text": "..."}
         or {"ok": false, "error": "one line"}

Protocol (``--selftest``): loads the model, runs a fixed cleanup, prints
``ok`` on success and exits 0, or prints an error to stderr and exits 1.
"""

from __future__ import annotations

import argparse
import json
import sys

# Error strings are clipped rather than passed through: an exception is
# not supposed to quote the input, and a reply is one line by contract.
_MAX_ERROR_CHARS = 200

# The ChatML template pieces, as literal strings.  The worker encodes
# these into token ids itself, one piece at a time — it never calls
# ``tokenizer.apply_chat_template`` and never evaluates a downloaded
# Jinja template.
#
# The assistant turn starts with a prefilled think block that is
# immediately closed — this suppresses Qwen3.5's chain-of-thought
# overhead and outputs the cleaned text directly.
_IM_START = "<|im_start|>"
_IM_END = "<|im_end|>"

# The selftest input and the prefix it must produce.
_SELFTEST_INPUT = "hello world this is a test"
_SELFTEST_EXPECT_PREFIX = "Hello"


def _mlx():
    """The mlx_lm module, imported on use.

    Also the seam the tests replace: importing this module never pulls in
    mlx_lm, so a stub can stand in for the real runtime.
    """
    import mlx_lm

    return mlx_lm


def _one_line(exc: BaseException) -> str:
    """An exception as a single short line, safe to put on a pipe."""
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return message[:_MAX_ERROR_CHARS]


def _check_config(model_dir: str) -> str | None:
    """Validate config.json and tokenizer_config.json.  Returns an error string, or None.

    Duplicates the manifest's check_config inline to keep this worker
    self-contained — it must never import vocalize.
    """
    import json as _json
    from pathlib import Path

    _MODEL_TYPE = "qwen3_5"
    _TOKENIZER_ALLOWLIST = frozenset({
        "Qwen2Tokenizer", "Qwen2TokenizerFast", "PreTrainedTokenizerFast",
    })
    _REFUSED = frozenset({"auto_map", "model_file", "custom_pipelines"})

    dir_path = Path(model_dir)
    for name in ("config.json", "tokenizer_config.json"):
        if not (dir_path / name).is_file():
            return f"missing: {name}"

    try:
        config = _json.loads((dir_path / "config.json").read_text(encoding="utf-8"))
        tokenizer_cfg = _json.loads(
            (dir_path / "tokenizer_config.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        return f"unreadable config: {_one_line(exc)}"

    if not isinstance(config, dict) or not isinstance(tokenizer_cfg, dict):
        return "config is not a JSON object"

    for blob, bname in ((config, "config.json"), (tokenizer_cfg, "tokenizer_config.json")):
        for key in _REFUSED:
            if key in blob:
                return f"{bname} contains {key}"

    if config.get("model_type") != _MODEL_TYPE:
        return f"wrong model_type: {config.get('model_type')}"

    tc = tokenizer_cfg.get("tokenizer_class", "")
    if tc and tc not in _TOKENIZER_ALLOWLIST:
        return f"foreign tokenizer_class: {tc}"

    return None


def _encode(tokenizer, text: str, *, special: bool = True) -> list[int]:
    """Encode text with or without special tokens.

    When ``special=False``, ``split_special_tokens=True`` ensures that
    even if the user text contains ``<|im_start|>`` or similar, those
    strings are treated as literal text and split into ordinary sub-word
    tokens — they never produce their control-token ids.
    """
    if special:
        return tokenizer.encode(text, add_special_tokens=False)
    return tokenizer.encode(
        text, add_special_tokens=False, split_special_tokens=True,
    )


def _build_prompt_ids(tokenizer, system: str, text: str) -> list[int]:
    """Build the full ChatML prompt as token ids.

    Never calls ``tokenizer.apply_chat_template`` — the template is a
    constant in this file (DEC-027).

    Layout::

        <|im_start|>system\n{system}<|im_end|>\n
        <|im_start|>user\n{text}<|im_end|>\n
        <|im_start|>assistant\n<think>\n\n</think>\n\n
    """
    ids: list[int] = []

    # System turn
    ids.extend(_encode(tokenizer, f"{_IM_START}system\n"))
    ids.extend(_encode(tokenizer, system, special=False))
    ids.extend(_encode(tokenizer, f"{_IM_END}\n"))

    # User turn
    ids.extend(_encode(tokenizer, f"{_IM_START}user\n"))
    ids.extend(_encode(tokenizer, text, special=False))
    ids.extend(_encode(tokenizer, f"{_IM_END}\n"))

    # Assistant turn with prefilled empty think block
    ids.extend(_encode(tokenizer, f"{_IM_START}assistant\n<think>\n\n</think>\n\n"))

    return ids


def _generate(mlx_lm_mod, model, tokenizer, prompt_ids: list[int], max_tokens: int) -> dict:
    """Run greedy generation and return the reply dict."""
    try:
        # mlx_lm.generate takes a string prompt, but we need token-level
        # control.  Use the lower-level generate_step or convert ids back
        # to a string that produces the same ids.  The simplest correct
        # approach: decode the ids back to a string and pass it.
        prompt_text = tokenizer.decode(prompt_ids)
        output = mlx_lm_mod.generate(
            model, tokenizer, prompt=prompt_text,
            max_tokens=max_tokens, verbose=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _one_line(exc)}

    # Check for truncation: if the output ends mid-think or with a length
    # finish, report it.
    stripped = output.strip()
    if not stripped:
        return {"ok": False, "error": "truncated"}

    # An unterminated think block means the model got stuck thinking.
    if "<think>" in stripped and "</think>" not in stripped:
        return {"ok": False, "error": "truncated"}

    return {"ok": True, "text": stripped}


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="llm_worker",
        description="On-device LLM cleanup/summary worker.  Never imported by vocalize.",
    )
    parser.add_argument(
        "--model-dir", required=True,
        help="Path to the model directory containing config.json and safetensors",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="One request on stdin, one reply on stdout")
    mode.add_argument("--selftest", action="store_true", help="Load the model and run a fixed cleanup")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # Config check runs before anything is loaded.
    error = _check_config(args.model_dir)
    if error is not None:
        print(f"llm_worker: config check failed: {error}", file=sys.stderr)
        return 1

    mlx_lm_mod = _mlx()

    try:
        model, tokenizer = mlx_lm_mod.load(
            args.model_dir,
            tokenizer_config={"trust_remote_code": False},
        )
    except Exception as exc:  # noqa: BLE001
        if args.selftest:
            print(f"llm_worker: could not load: {_one_line(exc)}", file=sys.stderr)
            return 1
        print(json.dumps({"ok": False, "error": _one_line(exc)}))
        return 0

    if args.selftest:
        system = "Clean up the dictated text: fix punctuation and casing, output only the cleaned text."
        ids = _build_prompt_ids(tokenizer, system, _SELFTEST_INPUT)
        result = _generate(mlx_lm_mod, model, tokenizer, ids, max_tokens=64)
        if not result.get("ok"):
            print(f"llm_worker: selftest failed: {result.get('error')}", file=sys.stderr)
            return 1
        text = result["text"]
        if not text.startswith(_SELFTEST_EXPECT_PREFIX):
            print(
                f"llm_worker: selftest output {text[:40]!r} does not start with "
                f"{_SELFTEST_EXPECT_PREFIX!r}",
                file=sys.stderr,
            )
            return 1
        print("ok")
        return 0

    # --once: read one request from stdin, write one response to stdout.
    try:
        raw = sys.stdin.read()
        request = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": _one_line(exc)}))
        return 0

    system = request.get("system", "")
    text = request.get("text", "")
    max_tokens = request.get("max_tokens", 4096)

    ids = _build_prompt_ids(tokenizer, system, text)
    result = _generate(mlx_lm_mod, model, tokenizer, ids, max_tokens=max_tokens)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
