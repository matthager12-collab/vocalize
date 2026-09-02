"""The pinned Whisper manifest is the security boundary: guard its shape."""

import re

import pytest

from vocalize.local import whisper_manifest as manifest


def test_there_are_exactly_the_three_models_the_spike_measured():
    assert set(manifest.MODELS) == {"base.en", "small.en", "large-v3-turbo-q5_0"}


def test_downloads_are_pinned_to_one_revision_over_https():
    assert manifest.RELEASE_URL.startswith("https://")
    assert manifest.REVISION in manifest.RELEASE_URL
    # A moving branch name is exactly what a pin must not be.
    assert "/main/" not in manifest.RELEASE_URL
    for entry in manifest.FILES:
        assert entry["url"].startswith(manifest.RELEASE_URL + "/")
        assert entry["url"].endswith(entry["name"])


def test_every_file_carries_a_size_and_a_full_sha256():
    assert len(manifest.FILES) == 3
    for entry in manifest.FILES:
        assert entry["size"] > 0
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry["name"]


def test_hashes_match_the_spike_report():
    # docs/plans/2026-09-next-features/spike-2026-09-01.md, verified from
    # completed downloads. Pinned exactly: change only alongside a fresh,
    # checked download.
    expected = {
        "ggml-base.en.bin": "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
        "ggml-small.en.bin": "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d",
        "ggml-large-v3-turbo-q5_0.bin": (
            "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"
        ),
    }
    assert {entry["name"]: entry["sha256"] for entry in manifest.FILES} == expected


def test_every_model_name_is_safe_to_pass_as_an_argument_or_file_name():
    # No dots-as-traversal, slashes, or leading dashes can survive this —
    # what keeps a config value or --model flag from becoming a flag or a
    # path when it turns into "ggml-<model>.bin".
    for model in manifest.MODELS:
        assert "/" not in model and ".." not in model
        assert not model.startswith("-")


def test_the_default_model_is_one_of_them():
    assert manifest.DEFAULT_MODEL in manifest.MODELS
    assert manifest.DEFAULT_MODEL == "small.en"


def test_the_runtime_package_is_version_pinned():
    assert manifest.RUNTIME_PACKAGE == "pywhispercpp==1.5.1"
    assert "==" in manifest.RUNTIME_PACKAGE


def test_the_weights_are_ggml_bin_files():
    # No pickle, no .pt, nothing torch.load would open.
    for entry in manifest.FILES:
        assert entry["name"].startswith("ggml-")
        assert entry["name"].endswith(".bin")


def test_the_worker_script_is_there():
    path = manifest.worker_path()
    assert path.is_absolute()
    assert path.is_file()
    assert path.name == "whisper_worker.py"


def test_model_path_follows_the_model_dir(tmp_path):
    path = manifest.model_path("small.en", tmp_path)
    assert path == tmp_path / "ggml-small.en.bin"
    assert manifest.model_path("small.en").parent == manifest.MODEL_DIR


@pytest.mark.parametrize(
    "bad_model",
    ["../../../../etc/passwd", "small.en\x00.sh", "--serve"],
    ids=["traversal", "control-character", "flag-shaped"],
)
def test_model_path_rejects_anything_off_the_allowlist(bad_model, tmp_path):
    # model_path() must enforce MODELS itself (via file_for), not trust a
    # caller upstream to have checked already — cli.py's own guard is one
    # of several ways to reach here.
    with pytest.raises(KeyError):
        manifest.model_path(bad_model, tmp_path)


@pytest.mark.parametrize(
    "bad_model",
    ["../../../../etc/passwd", "small.en\x00.sh", "--serve"],
    ids=["traversal", "control-character", "flag-shaped"],
)
def test_selftest_argv_rejects_anything_off_the_allowlist(bad_model, tmp_path):
    with pytest.raises(KeyError):
        manifest.selftest_argv(tmp_path, model=bad_model)


def test_file_for_returns_the_matching_manifest_entry():
    entry = manifest.file_for("small.en")
    assert entry["name"] == "ggml-small.en.bin"


def test_file_for_an_unknown_model_raises_instead_of_guessing():
    with pytest.raises(KeyError):
        manifest.file_for("../etc/passwd")


# --- language allowlist -------------------------------------------------


def test_language_allowlist_contains_english_and_auto():
    assert "en" in manifest.LANGUAGES
    assert "auto" in manifest.LANGUAGES


def test_language_allowlist_entries_are_short_lowercase_codes():
    for code in manifest.LANGUAGES:
        assert code.isalpha() and code.islower() and len(code) <= 4, code


def test_is_english_only_matches_the_dot_en_suffix():
    assert manifest.is_english_only("small.en") is True
    assert manifest.is_english_only("base.en") is True
    assert manifest.is_english_only("large-v3-turbo-q5_0") is False


# --- selftest_argv --------------------------------------------------------


def test_selftest_argv_names_the_selected_models_own_file(tmp_path):
    argv = manifest.selftest_argv(tmp_path, model="base.en")

    # No leading `uv` here: install.py owns prepending the executable.
    assert argv[:6] == [
        "run", "--no-project",
        "--python", manifest.PYTHON_VERSION,
        "--with", manifest.RUNTIME_PACKAGE,
    ]
    assert argv[6] == str(manifest.worker_path())
    assert argv[argv.index("--model") + 1] == str(tmp_path / "ggml-base.en.bin")
    assert argv[-1] == "--selftest"


def test_selftest_argv_forces_english_for_an_en_model():
    argv = manifest.selftest_argv(model="small.en")
    assert argv[argv.index("--language") + 1] == "en"


def test_selftest_argv_uses_auto_for_a_multilingual_model():
    argv = manifest.selftest_argv(model="large-v3-turbo-q5_0")
    assert argv[argv.index("--language") + 1] == "auto"


def test_selftest_argv_defaults_to_the_default_model(tmp_path):
    argv = manifest.selftest_argv(tmp_path)
    assert argv[argv.index("--model") + 1] == str(tmp_path / "ggml-small.en.bin")
