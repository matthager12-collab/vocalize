"""vocalize/local/llm_manifest.py: shape, hardening and argv contracts.

Every test here validates the manifest's *structure*, not its hashes —
the sha256 values are placeholder zeros until the owner runs a verified
download.  What the tests *do* prove is that the refusal surface in
``check_config`` catches every case the design lists, that URLs are
https-only, that no downloaded file is code, and that the selftest and
runtime argvs differ in exactly ``--offline`` / ``--selftest`` vs
``--once``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vocalize.local import llm_manifest as manifest

# --- URL and file-name allowlisting ------------------------------------


def test_every_url_is_https():
    for entry in manifest.FILES:
        assert entry["url"].startswith("https://"), entry["name"]


def test_no_executable_files():
    """No .py, .jinja, .sh, .bat, .exe — nothing that could be evaluated."""
    bad = {".py", ".jinja", ".jinja2", ".sh", ".bat", ".exe", ".dll", ".so", ".dylib"}
    for entry in manifest.FILES:
        suffix = Path(entry["name"]).suffix
        assert suffix not in bad, f"{entry['name']} has a forbidden extension"


def test_file_names_are_allowlisted():
    """Every file is one of the expected names."""
    allowed = {
        "config.json", "tokenizer.json", "tokenizer_config.json",
        "model.safetensors", "model.safetensors.index.json",
    }
    for entry in manifest.FILES:
        assert entry["name"] in allowed, f"unexpected file: {entry['name']}"


# --- check_config refusal surface --------------------------------------


def _write_configs(tmp_path, config=None, tokenizer=None):
    """Write config.json and tokenizer_config.json with sane defaults."""
    c = {"model_type": manifest.MODEL_TYPE, **(config or {})}
    t = {"tokenizer_class": "Qwen2TokenizerFast", **(tokenizer or {})}
    (tmp_path / "config.json").write_text(json.dumps(c), encoding="utf-8")
    (tmp_path / "tokenizer_config.json").write_text(json.dumps(t), encoding="utf-8")


def test_check_config_accepts_valid(tmp_path):
    _write_configs(tmp_path)
    manifest.check_config(tmp_path)  # must not raise


@pytest.mark.parametrize("key", ["auto_map", "model_file", "custom_pipelines"])
def test_check_config_refuses_dangerous_key_in_config(tmp_path, key):
    _write_configs(tmp_path, config={key: "anything"})
    with pytest.raises(ValueError, match=key):
        manifest.check_config(tmp_path)


@pytest.mark.parametrize("key", ["auto_map", "model_file", "custom_pipelines"])
def test_check_config_refuses_dangerous_key_in_tokenizer(tmp_path, key):
    _write_configs(tmp_path, tokenizer={key: "anything"})
    with pytest.raises(ValueError, match=key):
        manifest.check_config(tmp_path)


def test_check_config_refuses_wrong_model_type(tmp_path):
    _write_configs(tmp_path, config={"model_type": "llama"})
    with pytest.raises(ValueError, match="model_type"):
        manifest.check_config(tmp_path)


def test_check_config_refuses_foreign_tokenizer_class(tmp_path):
    _write_configs(tmp_path, tokenizer={"tokenizer_class": "MyEvilTokenizer"})
    with pytest.raises(ValueError, match="tokenizer_class"):
        manifest.check_config(tmp_path)


@pytest.mark.parametrize("cls", sorted(manifest._TOKENIZER_CLASS_ALLOWLIST))
def test_check_config_accepts_allowlisted_tokenizer_class(tmp_path, cls):
    _write_configs(tmp_path, tokenizer={"tokenizer_class": cls})
    manifest.check_config(tmp_path)  # must not raise


def test_check_config_refuses_missing_config(tmp_path):
    (tmp_path / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        manifest.check_config(tmp_path)


def test_check_config_refuses_missing_tokenizer(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": manifest.MODEL_TYPE}), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing"):
        manifest.check_config(tmp_path)


# --- argv contracts ----------------------------------------------------


def test_selftest_argv_is_online():
    argv = manifest.selftest_argv(Path("/fake"))
    assert "--offline" not in argv
    assert "--selftest" in argv
    assert "--once" not in argv


def test_runtime_argv_is_offline():
    argv = manifest.runtime_argv(Path("/fake"))
    assert "--offline" in argv
    assert "--once" in argv
    assert "--selftest" not in argv


def test_selftest_and_runtime_differ_only_by_offline_and_mode():
    """The two argvs must differ in exactly --offline and --selftest/--once."""
    selftest = set(manifest.selftest_argv(Path("/fake")))
    runtime = set(manifest.runtime_argv(Path("/fake")))
    only_selftest = selftest - runtime
    only_runtime = runtime - selftest
    assert only_selftest == {"--selftest"}, f"selftest-only: {only_selftest}"
    assert only_runtime == {"--offline", "--once"}, f"runtime-only: {only_runtime}"


def test_runtime_package_is_recorded():
    """The runtime package string is present so the stamp can record it."""
    assert manifest.RUNTIME_PACKAGE.startswith("mlx-lm==")


def test_worker_path_points_to_this_package():
    path = manifest.worker_path()
    assert path.name == "llm_worker.py"
    assert path.parent.name == "local"


# --- MIN_RAM_BYTES -----------------------------------------------------


def test_min_ram_is_12_gib():
    assert manifest.MIN_RAM_BYTES == 12 * 1024**3
