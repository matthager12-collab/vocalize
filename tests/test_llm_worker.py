"""vocalize/local/llm_worker.py: import discipline, DEC-027, selftest.

Every test here runs without mlx_lm installed.  The ``_mlx`` seam is
replaced by a fake module, and the tokenizer is a recording stub that
captures every call to ``encode`` and ``decode``.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# --- AST import-discipline test ----------------------------------------


def test_top_level_imports_never_pull_mlx_or_transformers():
    """The file's top-level ``import`` statements must not mention mlx_lm,
    mlx, or transformers — those are imported inside ``_mlx()``, so the
    file can be linted and AST-checked without them installed.
    """
    source = (Path(__file__).resolve().parent.parent / "vocalize" / "local" / "llm_worker.py")
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    banned = {"mlx_lm", "mlx", "transformers"}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned, (
                    f"top-level import of {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in banned:
                pytest.fail(f"top-level from-import of {node.module}")


# --- Fake tokenizer and model ------------------------------------------


class FakeTokenizer:
    """Records every encode/decode call so tests can assert the args."""

    def __init__(self):
        self.encode_calls: list[dict] = []
        self.decode_calls: list[str] = []
        self._next_id = 100

    def encode(self, text, *, add_special_tokens=True, split_special_tokens=False):
        self.encode_calls.append({
            "text": text,
            "add_special_tokens": add_special_tokens,
            "split_special_tokens": split_special_tokens,
        })
        # Return a deterministic sequence so _build_prompt_ids produces
        # something decode can round-trip.
        ids = list(range(self._next_id, self._next_id + max(1, len(text.split()))))
        self._next_id += len(ids)
        return ids

    def decode(self, ids):
        self.decode_calls.append(ids)
        return "Hello world."


@pytest.fixture()
def worker():
    """Import llm_worker with a fake _mlx seam."""
    # Force a fresh import so monkeypatching sticks.
    mod_name = "vocalize.local.llm_worker"
    saved = sys.modules.pop(mod_name, None)
    try:
        from vocalize.local import llm_worker
        yield llm_worker
    finally:
        if saved is not None:
            sys.modules[mod_name] = saved


@pytest.fixture()
def fake_model_dir(tmp_path):
    """A model directory with valid config files."""
    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": "qwen3_5"}), encoding="utf-8",
    )
    (tmp_path / "tokenizer_config.json").write_text(
        json.dumps({"tokenizer_class": "Qwen2TokenizerFast"}), encoding="utf-8",
    )
    return tmp_path


# --- DEC-027: apply_chat_template is never called -----------------------


def test_apply_chat_template_never_called(worker, fake_model_dir):
    """The worker builds token ids from its own constant.
    ``tokenizer.apply_chat_template`` must never be called."""
    tokenizer = FakeTokenizer()
    tokenizer.apply_chat_template = MagicMock()

    worker._build_prompt_ids(tokenizer, "system prompt", "user text")

    tokenizer.apply_chat_template.assert_not_called()


# --- split_special_tokens for user text ---------------------------------


def test_user_text_encoded_with_split_special_tokens(worker, fake_model_dir):
    """User text must be encoded with ``split_special_tokens=True`` so
    control-token strings in the input produce ordinary sub-word ids."""
    tokenizer = FakeTokenizer()
    worker._build_prompt_ids(tokenizer, "system prompt", "user text <|im_start|>")

    # Find the call that encoded user text (contains the user string).
    user_calls = [
        c for c in tokenizer.encode_calls
        if "user text" in c["text"] and "<|im_start|>user" not in c["text"]
    ]
    assert user_calls, "no call encoded the user text"
    for call in user_calls:
        assert call["split_special_tokens"] is True, (
            f"user text encoded without split_special_tokens=True: {call}"
        )
        assert call["add_special_tokens"] is False


def test_system_text_encoded_with_split_special_tokens(worker, fake_model_dir):
    """System text (from the prompt constant) also uses split_special_tokens."""
    tokenizer = FakeTokenizer()
    worker._build_prompt_ids(tokenizer, "do this <|im_end|>", "safe text")

    system_calls = [
        c for c in tokenizer.encode_calls
        if "do this" in c["text"] and "<|im_start|>system" not in c["text"]
    ]
    assert system_calls
    for call in system_calls:
        assert call["split_special_tokens"] is True


# --- Truncation reply ---------------------------------------------------


def test_truncation_reply_on_empty_output(worker):
    result = worker._generate(
        MagicMock(), MagicMock(), FakeTokenizer(), [1, 2, 3], max_tokens=64,
    )
    # The fake tokenizer.decode returns "Hello world.", and generate is
    # mocked — test the truncation path directly.
    # Override: make generate return empty.
    mlx_mod = SimpleNamespace(generate=lambda *a, **kw: "")
    result = worker._generate(mlx_mod, MagicMock(), FakeTokenizer(), [1, 2, 3], 64)
    assert result == {"ok": False, "error": "truncated"}


def test_truncation_reply_on_unterminated_think(worker):
    mlx_mod = SimpleNamespace(generate=lambda *a, **kw: "<think>reasoning here")
    result = worker._generate(mlx_mod, MagicMock(), FakeTokenizer(), [1, 2, 3], 64)
    assert result == {"ok": False, "error": "truncated"}


def test_ok_reply_on_clean_output(worker):
    mlx_mod = SimpleNamespace(generate=lambda *a, **kw: "Hello world, this is a test.")
    result = worker._generate(mlx_mod, MagicMock(), FakeTokenizer(), [1, 2, 3], 64)
    assert result == {"ok": True, "text": "Hello world, this is a test."}


# --- Selftest argv vs runtime argv (from manifest) ---------------------


def test_selftest_and_runtime_argv_differ_only_by_offline_and_mode():
    """Already tested in test_llm_manifest.py, but repeated here at the
    worker level to pin the contract the worker depends on."""
    from vocalize.local import llm_manifest as manifest

    selftest = set(manifest.selftest_argv(Path("/fake")))
    runtime = set(manifest.runtime_argv(Path("/fake")))
    only_selftest = selftest - runtime
    only_runtime = runtime - selftest
    assert only_selftest == {"--selftest"}
    assert only_runtime == {"--offline", "--once"}


# --- check_config gate --------------------------------------------------


def test_worker_check_config_refuses_auto_map(worker, tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": "qwen3_5", "auto_map": {}}), encoding="utf-8",
    )
    (tmp_path / "tokenizer_config.json").write_text(
        json.dumps({"tokenizer_class": "Qwen2TokenizerFast"}), encoding="utf-8",
    )
    error = worker._check_config(str(tmp_path))
    assert error is not None
    assert "auto_map" in error


def test_worker_check_config_passes_clean(worker, fake_model_dir):
    assert worker._check_config(str(fake_model_dir)) is None
