"""The pinned manifest is the security boundary: guard its shape."""

import re

from vocalize.local import kokoro_manifest as manifest

VOICE_RE = re.compile(r"^[a-z]{2}_[a-z]+$")


def test_the_voice_pack_has_its_54_ids():
    assert len(manifest.VOICES) == 54
    assert len(set(manifest.VOICES)) == 54


def test_every_voice_id_is_safe_to_pass_as_an_argument():
    # No dots, slashes, or leading dashes can survive this pattern, which
    # is what keeps a config value from becoming a flag or a path.
    for voice in manifest.VOICES:
        assert VOICE_RE.fullmatch(voice), voice


def test_the_default_voice_is_one_of_them():
    assert manifest.DEFAULT_VOICE in manifest.VOICES


def test_downloads_are_pinned_to_the_release_tag_over_https():
    assert manifest.RELEASE_URL.startswith("https://")
    assert manifest.RELEASE_TAG in manifest.RELEASE_URL
    for entry in manifest.FILES:
        assert entry["url"].startswith(manifest.RELEASE_URL + "/")
        assert entry["url"].endswith(entry["name"])


def test_every_file_carries_a_size_and_a_full_sha256():
    assert len(manifest.FILES) == 2
    for entry in manifest.FILES:
        assert entry["size"] > 0
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry["name"]


def test_the_weights_are_onnx_and_the_voice_pack_is_a_bin():
    # No pickle, no .pt, nothing torch.load would open.
    assert manifest.MODEL_FILE.endswith(".onnx")
    assert manifest.VOICES_FILE.endswith(".bin")


def test_the_runtime_package_is_version_pinned():
    assert "==" in manifest.RUNTIME_PACKAGE


def test_the_worker_script_is_there():
    path = manifest.worker_path()
    assert path.is_absolute()
    assert path.is_file()
    assert path.name == "kokoro_worker.py"


def test_file_paths_follow_the_model_dir(tmp_path):
    model, voices = manifest.file_paths(tmp_path)
    assert model == tmp_path / manifest.MODEL_FILE
    assert voices == tmp_path / manifest.VOICES_FILE
    assert manifest.file_paths()[0].parent == manifest.MODEL_DIR


def test_selftest_argv_reproduces_the_pre_generalization_hardcoded_list(tmp_path):
    """T-21 moved this argv out of install.py's selftest() and into the
    manifest that owns it. The list below is exactly what install.py used
    to build inline — this test is the proof nothing shifted in the move.
    """
    model, voices = manifest.file_paths(tmp_path)

    assert manifest.selftest_argv(tmp_path) == [
        "run", "--no-project",
        "--python", manifest.PYTHON_VERSION,
        "--with", manifest.RUNTIME_PACKAGE,
        str(manifest.worker_path()),
        "--model", str(model),
        "--voices", str(voices),
        "--voice", manifest.DEFAULT_VOICE,
        "--selftest",
    ]
