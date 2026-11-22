"""`vocalize local install` — the download, the verification, the command.

Every test runs against a fake opener and a tiny two-file manifest, so
the suite never downloads 354 MB, never reaches the network, and never
writes outside tmp_path.
"""

import hashlib
import http.client
import json
import stat
import subprocess
import tempfile
import types
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from click.testing import CliRunner

from vocalize import local as local_pkg
from vocalize.cli import _human_readable_size, main
from vocalize.local import install as install_module
from vocalize.local import kokoro_manifest as manifest
from vocalize.local import locks
from vocalize.local import whisper_manifest as stt_manifest
from vocalize.providers import kokoro as provider

MODEL_PAYLOAD = b"onnx-weights-pretend" * 10
VOICES_PAYLOAD = b"voice-pack-pretend" * 10


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._pos = 0

    def read(self, size):
        block = self._payload[self._pos : self._pos + size]
        self._pos += len(block)
        return block

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_for(payloads: dict):
    """A stand-in for urlopen. Serves bytes by URL, records every call."""
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        return FakeResponse(payloads[url])

    opener.calls = calls
    return opener


@pytest.fixture(autouse=True)
def _private_envs(tmp_path, monkeypatch):
    """Locked environments land under tmp_path, never the real cache."""
    monkeypatch.setattr(locks, "ENVS_DIR", tmp_path / "envs")


@pytest.fixture
def tiny_manifest(tmp_path, monkeypatch):
    """The real file names and URLs, with payloads a test can afford."""
    files = [
        {
            "name": manifest.MODEL_FILE,
            "url": f"{manifest.RELEASE_URL}/{manifest.MODEL_FILE}",
            "size": len(MODEL_PAYLOAD),
            "sha256": sha(MODEL_PAYLOAD),
        },
        {
            "name": manifest.VOICES_FILE,
            "url": f"{manifest.RELEASE_URL}/{manifest.VOICES_FILE}",
            "size": len(VOICES_PAYLOAD),
            "sha256": sha(VOICES_PAYLOAD),
        },
    ]
    monkeypatch.setattr(manifest, "FILES", files)
    monkeypatch.setattr(manifest, "MODEL_DIR", tmp_path / "models" / "kokoro")
    return files


@pytest.fixture
def payloads(tiny_manifest):
    return {
        tiny_manifest[0]["url"]: MODEL_PAYLOAD,
        tiny_manifest[1]["url"]: VOICES_PAYLOAD,
    }


# --- download_file ----------------------------------------------------


def test_a_good_download_lands_on_part_first_then_renames(tmp_path, tiny_manifest, payloads):
    entry = tiny_manifest[0]
    dest = tmp_path / "models" / "kokoro" / entry["name"]
    seen = []

    def opener(url, timeout=None):
        # The real file must not exist yet — only the .part beside it.
        seen.append((dest.exists(), dest.with_name(dest.name + ".part").exists()))
        return FakeResponse(payloads[url])

    install_module.download_file(
        entry["url"], dest, entry["size"], entry["sha256"], opener=opener
    )

    assert dest.read_bytes() == MODEL_PAYLOAD
    assert not dest.with_name(dest.name + ".part").exists()
    assert seen == [(False, False)]


def test_a_bad_hash_deletes_the_part_file_and_refuses(tmp_path, tiny_manifest, payloads):
    entry = tiny_manifest[0]
    dest = tmp_path / entry["name"]

    with pytest.raises(install_module.InstallError) as excinfo:
        install_module.download_file(
            entry["url"], dest, entry["size"], "0" * 64, opener=opener_for(payloads)
        )

    assert entry["name"] in str(excinfo.value)
    assert "checksum" in str(excinfo.value)
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()
    assert list(tmp_path.iterdir()) == []


def test_a_wrong_size_deletes_the_part_file_and_refuses(tmp_path, tiny_manifest, payloads):
    entry = tiny_manifest[0]
    dest = tmp_path / entry["name"]

    with pytest.raises(install_module.InstallError, match="wrong size"):
        install_module.download_file(
            entry["url"], dest, entry["size"] + 1, entry["sha256"],
            opener=opener_for(payloads),
        )

    assert list(tmp_path.iterdir()) == []


def test_plain_http_is_refused_before_anything_is_opened(tmp_path):
    opener = opener_for({})

    with pytest.raises(install_module.InstallError, match="insecure"):
        install_module.download_file(
            "http://example.com/model.onnx", tmp_path / "model.onnx", 10, "0" * 64,
            opener=opener,
        )

    assert opener.calls == []
    assert list(tmp_path.iterdir()) == []


def test_a_network_failure_leaves_nothing_behind(tmp_path, tiny_manifest):
    entry = tiny_manifest[0]

    def broken(url, timeout=None):
        raise OSError("connection reset")

    with pytest.raises(install_module.InstallError, match="Could not download"):
        install_module.download_file(
            entry["url"], tmp_path / entry["name"], entry["size"], entry["sha256"],
            opener=broken,
        )

    assert list(tmp_path.iterdir()) == []


def test_progress_is_reported_against_the_expected_size(tmp_path, tiny_manifest, payloads):
    entry = tiny_manifest[0]
    seen = []

    install_module.download_file(
        entry["url"], tmp_path / entry["name"], entry["size"], entry["sha256"],
        opener=opener_for(payloads), progress=lambda done, total: seen.append((done, total)),
    )

    assert seen[-1] == (entry["size"], entry["size"])


def test_a_runaway_response_is_stopped_before_it_overshoots(tmp_path, tiny_manifest):
    """A hostile or hung server that never sends an empty block must not
    stream an unbounded body to disk before the size is even checked."""
    entry = tiny_manifest[0]
    dest = tmp_path / entry["name"]

    class RunawayResponse:
        # A safety cap only: on the fixed code this is never approached
        # (one oversized block is enough to trip the guard). On the old
        # code, without this cap the loop would run until disk fills up.
        _SAFETY_CAP = 5

        def __init__(self):
            self.blocks_served = 0

        def read(self, size):
            if self.blocks_served >= self._SAFETY_CAP:
                return b""
            self.blocks_served += 1
            return b"x" * size

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    response = RunawayResponse()

    with pytest.raises(install_module.InstallError, match="larger than expected"):
        install_module.download_file(
            entry["url"], dest, entry["size"], entry["sha256"],
            opener=lambda url, timeout=None: response,
        )

    # RED on the old code: it only compares size after the loop ends, so
    # it would read all 5 capped blocks (5 MB) instead of stopping at 1,
    # and would raise "wrong size" instead of "larger than expected".
    assert response.blocks_served == 1
    assert not dest.with_name(dest.name + ".part").exists()
    assert list(tmp_path.iterdir()) == []


def test_an_incomplete_read_is_cleaned_up_like_any_other_network_error(
    tmp_path, tiny_manifest
):
    entry = tiny_manifest[0]
    dest = tmp_path / entry["name"]

    class BrokenResponse:
        def read(self, size):
            raise http.client.IncompleteRead(b"partial")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    # RED on the old code: `except (URLError, OSError)` does not catch
    # http.client.HTTPException, so IncompleteRead escapes as itself
    # (not InstallError) and the .part file is never unlinked.
    with pytest.raises(install_module.InstallError, match="Could not download"):
        install_module.download_file(
            entry["url"], dest, entry["size"], entry["sha256"],
            opener=lambda url, timeout=None: BrokenResponse(),
        )

    assert not dest.with_name(dest.name + ".part").exists()
    assert list(tmp_path.iterdir()) == []


def test_a_keyboard_interrupt_mid_download_still_removes_the_part_file(
    tmp_path, tiny_manifest
):
    entry = tiny_manifest[0]
    dest = tmp_path / entry["name"]

    class InterruptingResponse:
        def read(self, size):
            raise KeyboardInterrupt()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    # RED on the old code: KeyboardInterrupt is a BaseException, not an
    # OSError, so it skips the cleanup handler entirely and strands the
    # .part file even though it does propagate.
    with pytest.raises(KeyboardInterrupt):
        install_module.download_file(
            entry["url"], dest, entry["size"], entry["sha256"],
            opener=lambda url, timeout=None: InterruptingResponse(),
        )

    assert not dest.with_name(dest.name + ".part").exists()
    assert list(tmp_path.iterdir()) == []


# --- the https-only redirect handler -----------------------------------


def test_the_redirect_handler_refuses_a_downgrade_to_http():
    handler = install_module._HttpsOnlyRedirects()
    req = Request("https://github.com/owner/repo/releases/download/x/y", method="GET")

    with pytest.raises(HTTPError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://evil.example/y")


def test_the_redirect_handler_permits_a_same_scheme_hop():
    # The real case: github.com -> objects.githubusercontent.com, both https.
    handler = install_module._HttpsOnlyRedirects()
    req = Request("https://github.com/owner/repo/releases/download/x/y", method="GET")

    result = handler.redirect_request(
        req, None, 302, "Found", {}, "https://objects.githubusercontent.com/y"
    )

    assert result.full_url == "https://objects.githubusercontent.com/y"


# --- file_is_verified ---------------------------------------------------


def test_file_is_verified_when_present_and_matching(tmp_path, tiny_manifest):
    entry = tiny_manifest[0]
    (tmp_path / entry["name"]).write_bytes(MODEL_PAYLOAD)

    assert install_module.file_is_verified(entry, tmp_path) is True


def test_file_is_verified_is_false_on_wrong_size(tmp_path, tiny_manifest):
    entry = tiny_manifest[0]
    (tmp_path / entry["name"]).write_bytes(b"too short")

    assert install_module.file_is_verified(entry, tmp_path) is False


def test_file_is_verified_is_false_on_wrong_hash(tmp_path, tiny_manifest):
    entry = tiny_manifest[0]
    (tmp_path / entry["name"]).write_bytes(b"x" * entry["size"])  # right size, wrong bytes

    assert install_module.file_is_verified(entry, tmp_path) is False


def test_file_is_verified_is_false_when_missing(tmp_path, tiny_manifest):
    entry = tiny_manifest[0]

    assert install_module.file_is_verified(entry, tmp_path) is False


# --- the stamp --------------------------------------------------------


def test_the_stamp_round_trips(tmp_path, tiny_manifest):
    written = install_module.write_stamp(tmp_path)

    assert written.name == manifest.STAMP_NAME
    stamp = install_module.read_stamp(tmp_path)
    assert stamp["manifest_version"] == manifest.MANIFEST_VERSION
    assert set(stamp["files"]) == {manifest.MODEL_FILE, manifest.VOICES_FILE}
    assert stamp["files"][manifest.MODEL_FILE]["sha256"] == sha(MODEL_PAYLOAD)


def test_a_missing_or_corrupt_stamp_reads_as_none(tmp_path):
    assert install_module.read_stamp(tmp_path) is None

    install_module.stamp_path(tmp_path).write_text("{not json")
    assert install_module.read_stamp(tmp_path) is None

    install_module.stamp_path(tmp_path).write_text('"a string"')
    assert install_module.read_stamp(tmp_path) is None


def test_the_kokoro_stamp_stays_byte_identical_to_the_pre_generalization_format(
    tmp_path, tiny_manifest
):
    """T-21 gave write_stamp() `manifest=`/`files=` parameters so a second
    manifest (Whisper) can share it. Calling it the old way — model_dir
    only — must produce exactly the bytes the pre-generalization,
    Kokoro-only code wrote, because Kokoro's own installed() reads this
    file back and nothing about that contract may change.
    """
    written = install_module.write_stamp(tmp_path)

    expected = (
        json.dumps(
            {
                "manifest_version": manifest.MANIFEST_VERSION,
                "files": {
                    entry["name"]: {"size": entry["size"], "sha256": entry["sha256"]}
                    for entry in manifest.FILES
                },
            },
            indent=2,
        )
        + "\n"
    )
    assert written.read_text(encoding="utf-8") == expected


# --- installer generalization (T-21): a manifest=/files= seam, proven ---
# --- with a lightweight fake manifest before the real Whisper one exists.


def _fake_manifest(files, version=7):
    return types.SimpleNamespace(
        MODEL_DIR=None, STAMP_NAME=".verified", MANIFEST_VERSION=version, FILES=files,
    )


def test_write_stamp_with_a_files_subset_records_only_that_entry(tmp_path):
    fm = _fake_manifest([
        {"name": "a.bin", "size": 10, "sha256": "a" * 64},
        {"name": "b.bin", "size": 20, "sha256": "b" * 64},
    ])

    install_module.write_stamp(tmp_path, manifest=fm, files=[fm.FILES[0]])

    stamp = install_module.read_stamp(tmp_path, manifest=fm)
    assert set(stamp["files"]) == {"a.bin"}
    assert list(tmp_path.iterdir()) == [tmp_path / fm.STAMP_NAME]  # nothing outside tmp


def test_write_stamp_keeps_an_earlier_entry_when_a_different_one_is_added(tmp_path):
    """A model installed today must not go 'unverified' just because a
    later `local install --stt --model other` only re-verified the other
    one — otherwise switching models would silently break the first."""
    fm = _fake_manifest([
        {"name": "a.bin", "size": 10, "sha256": "a" * 64},
        {"name": "b.bin", "size": 20, "sha256": "b" * 64},
    ])

    install_module.write_stamp(tmp_path, manifest=fm, files=[fm.FILES[0]])
    install_module.write_stamp(tmp_path, manifest=fm, files=[fm.FILES[1]])

    stamp = install_module.read_stamp(tmp_path, manifest=fm)
    assert set(stamp["files"]) == {"a.bin", "b.bin"}


def test_write_stamp_drops_stale_entries_when_the_manifest_version_changes(tmp_path):
    fm_v1 = _fake_manifest([{"name": "a.bin", "size": 10, "sha256": "a" * 64}], version=1)
    fm_v2 = _fake_manifest([{"name": "c.bin", "size": 30, "sha256": "c" * 64}], version=2)

    install_module.write_stamp(tmp_path, manifest=fm_v1, files=fm_v1.FILES)
    install_module.write_stamp(tmp_path, manifest=fm_v2, files=fm_v2.FILES)

    stamp = install_module.read_stamp(tmp_path, manifest=fm_v2)
    assert stamp["manifest_version"] == 2
    assert set(stamp["files"]) == {"c.bin"}  # the v1 entry is gone, not merged


def test_installed_passes_with_a_manifest_and_a_files_subset(tmp_path):
    payload = b"xyz"
    fm = _fake_manifest([{"name": "a.bin", "size": len(payload), "sha256": sha(payload)}])
    (tmp_path / "a.bin").write_bytes(payload)
    install_module.write_stamp(tmp_path, manifest=fm)

    ready, reason = install_module.installed(fm, tmp_path)
    assert ready is True, reason


def test_installed_reports_the_given_install_hint(tmp_path):
    fm = _fake_manifest([{"name": "a.bin", "size": 3, "sha256": "0" * 64}])

    ready, reason = install_module.installed(
        fm, tmp_path, install_hint="vocalize local install --stt"
    )

    assert ready is False
    assert "vocalize local install --stt" in reason


def test_installed_is_false_when_a_files_subset_entry_is_missing(tmp_path):
    fm = _fake_manifest([{"name": "a.bin", "size": 3, "sha256": "0" * 64}])

    ready, reason = install_module.installed(fm, tmp_path, files=fm.FILES)

    assert ready is False
    assert "not installed" in reason


# --- a malformed .verified stamp is healed, never crashed on ------------


def test_write_stamp_heals_a_stamp_whose_files_value_is_a_list(tmp_path):
    # A truncated/hand-edited/foreign stamp — read_stamp() only rejects a
    # non-dict *top-level* JSON value, so {"files": [...]} gets through to
    # write_stamp()'s merge. A reinstall must replace it, not crash after
    # the download and verification already succeeded.
    fm = _fake_manifest([{"name": "a.bin", "size": 10, "sha256": "a" * 64}])
    install_module.stamp_path(tmp_path, fm).write_text(
        json.dumps({"manifest_version": fm.MANIFEST_VERSION, "files": ["oops"]})
    )

    install_module.write_stamp(tmp_path, manifest=fm, files=fm.FILES)

    stamp = install_module.read_stamp(tmp_path, manifest=fm)
    assert stamp["files"] == {"a.bin": {"size": 10, "sha256": "a" * 64}}


def test_write_stamp_drops_a_non_dict_entry_while_keeping_the_rest(tmp_path):
    fm = _fake_manifest([
        {"name": "a.bin", "size": 10, "sha256": "a" * 64},
        {"name": "b.bin", "size": 20, "sha256": "b" * 64},
    ])
    install_module.write_stamp(tmp_path, manifest=fm, files=[fm.FILES[0]])
    stamp_path = install_module.stamp_path(tmp_path, fm)
    data = json.loads(stamp_path.read_text())
    data["files"]["a.bin"] = "not-a-dict"  # e.g. a foreign or truncated stamp
    stamp_path.write_text(json.dumps(data))

    install_module.write_stamp(tmp_path, manifest=fm, files=[fm.FILES[1]])

    stamp = install_module.read_stamp(tmp_path, manifest=fm)
    assert stamp["files"] == {"b.bin": {"size": 20, "sha256": "b" * 64}}


def test_installed_treats_a_list_valued_files_stamp_as_nothing_recorded(tmp_path):
    payload = b"xyz"
    fm = _fake_manifest([{"name": "a.bin", "size": len(payload), "sha256": sha(payload)}])
    (tmp_path / "a.bin").write_bytes(payload)
    install_module.stamp_path(tmp_path, fm).write_text(
        json.dumps({"manifest_version": fm.MANIFEST_VERSION, "files": ["oops"]})
    )

    ready, reason = install_module.installed(fm, tmp_path)

    assert ready is False
    assert "does not match" in reason


# --- selftest ---------------------------------------------------------


def _make_venv(argv):
    """What a real `uv venv <dir>` leaves behind that the install checks."""
    if argv[1:2] == ["venv"]:
        python = Path(argv[-1]) / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")


def test_the_selftest_builds_the_locked_environment_then_runs_the_worker(tmp_path, tiny_manifest):
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        _make_venv(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    assert install_module.selftest("/usr/local/bin/uv", runner=runner) == "ok"

    lock = manifest.lock_file()
    env = locks.env_dir("kokoro", lock)
    assert env.parent == tmp_path / "envs"
    assert env.name == f"kokoro-{hashlib.sha256(lock.read_bytes()).hexdigest()}"
    assert [argv for argv, _ in calls[:2]] == [
        ["/usr/local/bin/uv", "venv", "--clear", "--no-project",
         "--python", manifest.PYTHON_VERSION, str(env)],
        ["/usr/local/bin/uv", "pip", "sync", "--python", str(env / "bin" / "python"),
         "--require-hashes", "--only-binary", ":all:", str(lock)],
    ]
    argv = calls[2][0]
    # --no-project + a neutral cwd: uv must never mistake the caller's
    # directory for a project and rebuild that project's .venv.
    assert argv[:8] == [
        "/usr/local/bin/uv", "run", "--no-project", "--no-build",
        "--offline", "--no-python-downloads",
        "--python", str(env / "bin" / "python"),
    ]
    assert "--with" not in argv
    assert argv[8] == str(manifest.worker_path())
    assert argv[-1] == "--selftest"
    assert all(kw["cwd"] == tempfile.gettempdir() for _, kw in calls)


def test_a_failed_hash_locked_install_leaves_the_runtime_not_installed(tmp_path, tiny_manifest):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["pip", "sync"]:
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="error: Hash mismatch for `numpy==2.5.4`\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with pytest.raises(install_module.InstallError, match="Hash mismatch"):
        install_module.selftest("uv", runner=runner)

    assert len(calls) == 2  # the worker never ran
    assert not install_module.runtime_installed(manifest)


def test_an_installed_locked_environment_is_not_rebuilt(tmp_path, tiny_manifest):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        _make_venv(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    install_module.selftest("uv", runner=runner)
    assert install_module.runtime_installed(manifest)
    calls.clear()

    install_module.selftest("uv", runner=runner)

    assert [argv[1] for argv in calls] == ["run"]


def test_a_changed_lock_needs_install_again(tmp_path, monkeypatch, tiny_manifest):
    lock = tmp_path / "kokoro.txt"
    lock.write_text("kokoro-onnx==0.6.1 \\\n    --hash=sha256:" + "a" * 64 + "\n")
    monkeypatch.setattr(manifest, "LOCK_FILE", lock)
    monkeypatch.setattr(manifest, "LOCK_FILE_X86_64", lock)
    python = locks.env_python("kokoro", lock)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    locks.write_marker("kokoro", lock)
    assert install_module.runtime_installed(manifest)

    lock.write_text(lock.read_text() + "numpy==2.5.4 \\\n    --hash=sha256:" + "b" * 64 + "\n")

    assert not install_module.runtime_installed(manifest)
    for entry in tiny_manifest:
        path = manifest.MODEL_DIR / entry["name"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MODEL_PAYLOAD if entry["name"] == manifest.MODEL_FILE else VOICES_PAYLOAD)
    install_module.write_stamp()
    ready, reason = install_module.installed(manifest)
    assert not ready
    assert reason == (
        "its locked runtime is missing or out of date — run `vocalize local install` again"
    )


def test_the_selftest_forwards_manifest_kwargs_to_a_second_manifests_argv(tmp_path):
    # Deleting **manifest_kwargs from install.py's
    # `manifest.selftest_argv(base, **manifest_kwargs)` call would still
    # leave the whole suite green if nothing exercised it with a manifest
    # other than Kokoro's (which takes no kwargs) — `local install --stt
    # --model base.en` would then warm whichever model the *manifest
    # default* names, not the one actually installed.
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        _make_venv(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    result = install_module.selftest(
        "/uv", manifest=stt_manifest, model_dir=tmp_path, runner=runner, model="base.en",
    )

    assert result == "ok"
    argv, kwargs = calls[-1]
    assert argv == ["/uv", *stt_manifest.selftest_argv(tmp_path, model="base.en")]
    assert "--no-project" in argv
    assert "base.en" in " ".join(argv)
    assert "large-v3-turbo-q5_0" not in " ".join(argv)  # the manifest default, not what was asked for
    assert kwargs["cwd"] == tempfile.gettempdir()


def test_a_failing_selftest_reports_the_last_stderr_line(tiny_manifest):
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="warming up\nkokoro: could not load the model\n"
        )

    with pytest.raises(install_module.InstallError, match="could not load the model"):
        install_module.selftest("uv", runner=runner)


# --- the CLI ----------------------------------------------------------


def _locked_selftest(record, *manifests):
    """A passing selftest that leaves what the real one does: an
    environment built from each manifest's current lock."""
    def selftest(uv, **kw):
        for m in manifests if not kw.get("manifest") else (kw["manifest"],):
            python = locks.env_python(m.ENGINE, m.lock_file())
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
            locks.write_marker(m.ENGINE, m.lock_file())
        return record(uv, kw) or "ok"
    return selftest


@pytest.fixture
def fake_install(monkeypatch, payloads, tiny_manifest):
    """Wire the command to the fake opener and a selftest that passes."""
    real_download = install_module.download_file
    downloads = []

    def download(url, dest, size, sha256, progress=None):
        downloads.append(dest)
        return real_download(url, dest, size, sha256, opener=opener_for(payloads),
                             progress=progress)

    selftests = []
    monkeypatch.setattr(install_module, "download_file", download)
    monkeypatch.setattr(install_module, "selftest",
                        _locked_selftest(lambda uv, kw: selftests.append(uv), manifest))
    # `local install` (Kokoro) resolves uv through the re-exported
    # provider.uv_path(); `local status` resolves it through
    # vocalize.local.uv_path() directly (see local_status()'s comment) —
    # both need patching so a test that runs install then status sees a
    # consistent uv on both commands.
    monkeypatch.setattr(provider, "uv_path", lambda: "/usr/local/bin/uv")
    monkeypatch.setattr(local_pkg, "uv_path", lambda: "/usr/local/bin/uv")
    return {"downloads": downloads, "selftests": selftests}


def test_install_prints_the_plan_and_stops_when_declined(fake_install, tiny_manifest):
    result = CliRunner().invoke(main, ["local", "install"], input="n\n")

    assert result.exit_code == 1
    for entry in tiny_manifest:
        assert entry["url"] in result.output
        assert entry["name"] in result.output
    assert str(manifest.MODEL_DIR) in result.output
    assert manifest.RUNTIME_PACKAGE in result.output
    assert "Aborted, nothing downloaded." in result.output
    # Declining downloads nothing and creates nothing.
    assert fake_install["downloads"] == []
    assert not manifest.MODEL_DIR.exists()


def test_install_with_yes_downloads_verifies_and_warms(fake_install, tiny_manifest):
    result = CliRunner().invoke(main, ["local", "install", "--yes"])

    assert result.exit_code == 0, result.output
    assert (manifest.MODEL_DIR / manifest.MODEL_FILE).read_bytes() == MODEL_PAYLOAD
    assert (manifest.MODEL_DIR / manifest.VOICES_FILE).read_bytes() == VOICES_PAYLOAD
    assert install_module.read_stamp() is not None
    assert fake_install["selftests"] == ["/usr/local/bin/uv"]
    assert "Warming the runtime" in result.output
    assert "--provider kokoro" in result.output
    # Nothing was written anywhere but the model directory under tmp_path.
    assert all(dest.parent == manifest.MODEL_DIR for dest in fake_install["downloads"])


def test_a_confirmed_install_downloads_the_same_way(fake_install):
    result = CliRunner().invoke(main, ["local", "install"], input="y\n")

    assert result.exit_code == 0, result.output
    assert len(fake_install["downloads"]) == 2


def test_a_second_install_says_so_and_downloads_nothing(fake_install):
    assert CliRunner().invoke(main, ["local", "install", "--yes"]).exit_code == 0
    fake_install["downloads"].clear()

    result = CliRunner().invoke(main, ["local", "install", "--yes"])

    assert result.exit_code == 0
    assert result.output.strip() == "Kokoro is already installed."
    assert fake_install["downloads"] == []


def test_install_stops_when_a_hash_does_not_match(monkeypatch, fake_install, tiny_manifest):
    monkeypatch.setitem(tiny_manifest[1], "sha256", "0" * 64)

    result = CliRunner().invoke(main, ["local", "install", "--yes"])

    assert result.exit_code == 1
    assert manifest.VOICES_FILE in result.output
    assert "checksum" in result.output
    # The bad file is gone, and the install never claims to be done.
    assert not (manifest.MODEL_DIR / manifest.VOICES_FILE).exists()
    assert not (manifest.MODEL_DIR / (manifest.VOICES_FILE + ".part")).exists()
    assert install_module.read_stamp() is None


def test_install_without_uv_downloads_nothing(monkeypatch, fake_install):
    monkeypatch.setattr(provider, "uv_path", lambda: None)

    result = CliRunner().invoke(main, ["local", "install", "--yes"])

    assert result.exit_code == 1
    assert "https://docs.astral.sh/uv/" in result.output
    assert fake_install["downloads"] == []
    assert not manifest.MODEL_DIR.exists()


def test_a_failing_selftest_keeps_the_verified_files(monkeypatch, fake_install):
    def boom(uv, **kwargs):
        raise install_module.InstallError("espeak-ng is missing")

    monkeypatch.setattr(install_module, "selftest", boom)

    result = CliRunner().invoke(main, ["local", "install", "--yes"])

    assert result.exit_code == 1
    assert "espeak-ng is missing" in result.output
    assert (manifest.MODEL_DIR / manifest.MODEL_FILE).exists()
    assert install_module.read_stamp() is not None


# --- local status -----------------------------------------------------


def test_status_before_anything_is_installed(monkeypatch, tiny_manifest):
    # `local status` resolves uv via vocalize.local.uv_path() (local_pkg),
    # not Kokoro's re-exported name — see local_status()'s comment.
    monkeypatch.setattr(local_pkg, "uv_path", lambda: None)

    result = CliRunner().invoke(main, ["local", "status"])

    assert result.exit_code == 0
    assert "uv: not found" in result.output
    assert f"{manifest.MODEL_FILE}: missing" in result.output
    assert f"{manifest.STAMP_NAME}: missing" in result.output
    assert "Kokoro is not usable" in result.output
    assert "vocalize local install" in result.output


def test_status_once_it_is_installed(fake_install):
    CliRunner().invoke(main, ["local", "install", "--yes"])

    result = CliRunner().invoke(main, ["local", "status"])

    assert result.exit_code == 0
    assert "uv: /usr/local/bin/uv" in result.output
    assert f"{manifest.MODEL_FILE}: present" in result.output
    assert f"{manifest.STAMP_NAME}: ok" in result.output
    assert "Kokoro is ready." in result.output


def test_status_notices_a_file_of_the_wrong_size(fake_install):
    CliRunner().invoke(main, ["local", "install", "--yes"])
    (manifest.MODEL_DIR / manifest.MODEL_FILE).write_bytes(b"short")

    result = CliRunner().invoke(main, ["local", "status"])

    assert f"{manifest.MODEL_FILE}: wrong size" in result.output
    assert "Kokoro is not usable" in result.output


def test_status_reports_a_stamp_that_belongs_to_another_release(fake_install):
    CliRunner().invoke(main, ["local", "install", "--yes"])
    stamp = install_module.stamp_path()
    data = json.loads(stamp.read_text())
    data["files"][manifest.MODEL_FILE]["sha256"] = "0" * 64
    stamp.write_text(json.dumps(data))

    result = CliRunner().invoke(main, ["local", "status"])

    assert "does not match this release" in result.output


# --- `vocalize local install --stt` (T-23) and `local uninstall --stt` ---
# --- (T-25) — a second manifest driving the same generalized installer ---

STT_PAYLOAD_BY_MODEL = {
    "base.en": b"base-tiny-weights" * 5,
    "small.en": b"small-tiny-weights" * 5,
    "large-v3-turbo-q5_0": b"turbo-tiny-weights" * 5,
    # Run 2 added the 8-bit turbo; the fake manifest must carry every real name
    # or `local status` (which walks MODELS) raises KeyError on the missing one.
    "large-v3-turbo-q8_0": b"ggml turbo eight bit weights, pretend",
}


@pytest.fixture
def tiny_stt_manifest(tmp_path, monkeypatch):
    """The real model names and URL shape, with payloads a test can afford."""
    files = [
        {
            "name": stt_manifest.model_file(model),
            "url": f"{stt_manifest.RELEASE_URL}/{stt_manifest.model_file(model)}",
            "size": len(payload),
            "sha256": sha(payload),
        }
        for model, payload in STT_PAYLOAD_BY_MODEL.items()
    ]
    monkeypatch.setattr(stt_manifest, "FILES", files)
    monkeypatch.setattr(stt_manifest, "MODEL_DIR", tmp_path / "models" / "whisper")
    return files


@pytest.fixture
def stt_payloads(tiny_stt_manifest):
    return {
        entry["url"]: STT_PAYLOAD_BY_MODEL[entry["name"][len("ggml-") : -len(".bin")]]
        for entry in tiny_stt_manifest
    }


@pytest.fixture
def fake_stt_install(monkeypatch, stt_payloads, tiny_stt_manifest):
    """Wire `local install --stt` to the fake opener and a passing selftest."""
    real_download = install_module.download_file
    downloads = []

    def download(url, dest, size, sha256, progress=None):
        downloads.append(dest)
        return real_download(
            url, dest, size, sha256, opener=opener_for(stt_payloads), progress=progress
        )

    selftests = []
    builds = []
    monkeypatch.setattr(install_module, "download_file", download)
    monkeypatch.setattr(
        install_module, "selftest",
        _locked_selftest(lambda uv, kw: selftests.append((uv, kw)), stt_manifest),
    )
    # The recorder build has its own suite (test_recorder_build.py) driving
    # the real function through a fake compiler; here it only has to not
    # reach for the machine's Swift toolchain.
    def build_recorder(**kwargs):
        builds.append(kwargs)
        return "built", install_module.recorder_bundle()

    monkeypatch.setattr(install_module, "build_recorder", build_recorder)
    monkeypatch.setattr(local_pkg, "uv_path", lambda: "/usr/local/bin/uv")
    return {"downloads": downloads, "selftests": selftests, "builds": builds}


def test_whisper_install_prints_the_plan_and_stops_when_declined(fake_stt_install):
    entry = stt_manifest.file_for(stt_manifest.DEFAULT_MODEL)

    result = CliRunner().invoke(main, ["local", "install", "--stt"], input="n\n")

    assert result.exit_code == 1
    assert entry["url"] in result.output
    assert entry["name"] in result.output
    assert str(stt_manifest.MODEL_DIR) in result.output
    assert stt_manifest.RUNTIME_PACKAGE in result.output
    assert "Aborted, nothing downloaded." in result.output
    assert fake_stt_install["downloads"] == []
    assert not stt_manifest.MODEL_DIR.exists()


def test_whisper_install_with_yes_downloads_verifies_and_warms(fake_stt_install):
    entry = stt_manifest.file_for(stt_manifest.DEFAULT_MODEL)

    result = CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])

    assert result.exit_code == 0, result.output
    assert (stt_manifest.MODEL_DIR / entry["name"]).read_bytes() == (
        STT_PAYLOAD_BY_MODEL[stt_manifest.DEFAULT_MODEL]
    )
    stamp = install_module.read_stamp(manifest=stt_manifest)
    assert stamp is not None
    assert set(stamp["files"]) == {entry["name"]}  # only the selected model is stamped
    assert fake_stt_install["selftests"] == [
        ("/usr/local/bin/uv", {"manifest": stt_manifest, "model": stt_manifest.DEFAULT_MODEL})
    ]
    assert "Warming the runtime" in result.output
    # `listen` has no --model flag (DEC-006: the model comes from [stt]
    # config) — the success message must never suggest one.
    assert f"Speech-to-text installed ({stt_manifest.DEFAULT_MODEL})" in result.output
    assert "vocalize listen --check" in result.output
    assert len(fake_stt_install["builds"]) == 1  # the recorder is built too
    assert "listen --model" not in result.output


def test_whisper_install_downloads_only_the_selected_model(fake_stt_install):
    result = CliRunner().invoke(main, ["local", "install", "--stt", "--model", "base.en", "--yes"])

    assert result.exit_code == 0, result.output
    assert len(fake_stt_install["downloads"]) == 1
    entry = stt_manifest.file_for("base.en")
    assert fake_stt_install["downloads"][0].name == entry["name"]
    for other in ("small.en", "large-v3-turbo-q5_0"):
        assert not (stt_manifest.MODEL_DIR / stt_manifest.file_for(other)["name"]).exists()


@pytest.mark.parametrize(
    "bad_model",
    ["nope", "../../../../etc/passwd", "small.en\x00.sh", "--serve"],
    ids=["unknown", "traversal", "control-character", "flag-shaped"],
)
def test_whisper_install_rejects_an_unknown_model(fake_stt_install, bad_model):
    result = CliRunner().invoke(main, ["local", "install", "--stt", "--model", bad_model, "--yes"])

    assert result.exit_code != 0
    assert "Unknown model" in result.output
    assert fake_stt_install["downloads"] == []


def test_the_model_flag_without_stt_is_rejected(fake_stt_install):
    result = CliRunner().invoke(main, ["local", "install", "--model", "small.en", "--yes"])

    assert result.exit_code != 0
    assert "--model only applies together with --stt" in result.output


def test_a_second_whisper_install_says_so_downloads_nothing_and_re_warms(fake_stt_install):
    assert CliRunner().invoke(main, ["local", "install", "--stt", "--yes"]).exit_code == 0
    fake_stt_install["downloads"].clear()
    fake_stt_install["selftests"].clear()
    fake_stt_install["builds"].clear()

    result = CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])

    assert result.exit_code == 0
    assert "already installed" in result.output
    assert fake_stt_install["downloads"] == []
    # The already-installed path still re-runs the selftest: a stamp is
    # written before the selftest ever succeeds, so "installed" and
    # "the runtime actually starts" are not the same fact.
    assert len(fake_stt_install["selftests"]) == 1
    # ...and still builds the recorder, so a re-run fixes a missing bundle
    # without re-downloading a verified 488 MB model.
    assert len(fake_stt_install["builds"]) == 1


def test_reinstall_retries_a_selftest_that_previously_failed(monkeypatch, fake_stt_install):
    # write_stamp() runs before selftest(), so a failed warm-up still
    # leaves the model "installed". The only in-CLI repair is running
    # install again — it must actually retry the selftest, not just
    # print "already installed" and exit 0.
    attempts = []

    def flaky_selftest(uv, **kw):
        attempts.append(kw)
        raise install_module.InstallError("Metal is unavailable")

    monkeypatch.setattr(install_module, "selftest", flaky_selftest)

    first = CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])
    assert first.exit_code == 1

    second = CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])

    assert second.exit_code == 1
    assert "would not start" in second.output
    assert len(attempts) == 2


def test_whisper_install_skips_an_already_verified_file(fake_stt_install, stt_payloads):
    entry = stt_manifest.file_for(stt_manifest.DEFAULT_MODEL)
    stt_manifest.MODEL_DIR.mkdir(parents=True)
    (stt_manifest.MODEL_DIR / entry["name"]).write_bytes(stt_payloads[entry["url"]])

    result = CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])

    assert result.exit_code == 0, result.output
    assert f"{entry['name']}: already verified, skipping" in result.output
    assert fake_stt_install["downloads"] == []


def test_whisper_install_stops_when_a_hash_does_not_match(monkeypatch, fake_stt_install):
    entry = stt_manifest.file_for(stt_manifest.DEFAULT_MODEL)
    monkeypatch.setitem(entry, "sha256", "0" * 64)

    result = CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])

    assert result.exit_code == 1
    assert "checksum" in result.output
    assert not (stt_manifest.MODEL_DIR / entry["name"]).exists()
    assert install_module.read_stamp(manifest=stt_manifest) is None


def test_whisper_install_without_uv_downloads_nothing(monkeypatch, fake_stt_install):
    monkeypatch.setattr(local_pkg, "uv_path", lambda: None)

    result = CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])

    assert result.exit_code == 1
    assert "https://docs.astral.sh/uv/" in result.output
    assert fake_stt_install["downloads"] == []
    assert not stt_manifest.MODEL_DIR.exists()


def test_a_failing_whisper_selftest_keeps_the_verified_file(monkeypatch, fake_stt_install):
    def boom(uv, **kwargs):
        raise install_module.InstallError("Metal is unavailable")

    monkeypatch.setattr(install_module, "selftest", boom)

    result = CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])

    assert result.exit_code == 1
    assert "Metal is unavailable" in result.output
    entry = stt_manifest.file_for(stt_manifest.DEFAULT_MODEL)
    assert (stt_manifest.MODEL_DIR / entry["name"]).exists()
    assert install_module.read_stamp(manifest=stt_manifest) is not None


# --- `local status`'s STT block ------------------------------------------


def test_status_whisper_before_anything_is_installed(tiny_stt_manifest):
    result = CliRunner().invoke(main, ["local", "status"])

    assert result.exit_code == 0
    assert "STT (speech-to-text):" in result.output
    assert "no models installed" in result.output
    assert "STT: not ready" in result.output


def test_status_whisper_once_the_default_model_is_installed(fake_stt_install):
    CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])

    result = CliRunner().invoke(main, ["local", "status"])

    entry = stt_manifest.file_for(stt_manifest.DEFAULT_MODEL)
    assert result.exit_code == 0
    # The full line, size included — a prefix match would still pass if
    # `_human_readable_size` were dropped from the status line entirely.
    assert (
        f"{entry['name']}: verified ({_human_readable_size(entry['size'])})"
        in result.output
    )
    assert f"STT: ready ({stt_manifest.DEFAULT_MODEL})" in result.output


def test_status_whisper_ready_after_a_non_default_model_install(fake_stt_install):
    # `local install --stt --model base.en` is a complete, working
    # install; status must not tell the user to redo it just because it
    # is not the default model.
    CliRunner().invoke(main, ["local", "install", "--stt", "--model", "base.en", "--yes"])

    result = CliRunner().invoke(main, ["local", "status"])

    assert result.exit_code == 0
    assert "STT: ready (base.en)" in result.output
    assert "STT: not ready" not in result.output


def test_status_whisper_names_a_present_but_unverified_file(fake_stt_install, stt_payloads):
    entry = stt_manifest.file_for(stt_manifest.DEFAULT_MODEL)
    stt_manifest.MODEL_DIR.mkdir(parents=True)
    (stt_manifest.MODEL_DIR / entry["name"]).write_bytes(stt_payloads[entry["url"]])

    result = CliRunner().invoke(main, ["local", "status"])

    assert f"{entry['name']}: present, not verified" in result.output
    assert "STT: not ready" in result.output


# --- `vocalize local uninstall --stt` (T-25) ------------------------------


def test_uninstall_whisper_says_nothing_to_remove_when_nothing_is_installed(tiny_stt_manifest):
    result = CliRunner().invoke(main, ["local", "uninstall", "--stt", "--yes"])

    assert result.exit_code == 0
    assert result.output.strip() == "Nothing to remove."


def test_uninstall_whisper_declined_leaves_everything(fake_stt_install):
    CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])
    entry = stt_manifest.file_for(stt_manifest.DEFAULT_MODEL)

    result = CliRunner().invoke(main, ["local", "uninstall", "--stt"], input="n\n")

    assert result.exit_code == 1
    assert "Aborted, nothing removed." in result.output
    assert (stt_manifest.MODEL_DIR / entry["name"]).exists()


def test_uninstall_whisper_with_yes_removes_the_model_directory(fake_stt_install):
    CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])

    result = CliRunner().invoke(main, ["local", "uninstall", "--stt", "--yes"])

    assert result.exit_code == 0
    assert "Speech-to-text uninstalled." in result.output
    assert not stt_manifest.MODEL_DIR.exists()


def test_uninstall_whisper_a_second_run_says_nothing_to_remove(fake_stt_install):
    CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])
    CliRunner().invoke(main, ["local", "uninstall", "--stt", "--yes"])

    result = CliRunner().invoke(main, ["local", "uninstall", "--stt", "--yes"])

    assert result.exit_code == 0
    assert result.output.strip() == "Nothing to remove."


def test_uninstall_whisper_removes_the_model_dir_the_recorder_bundle_and_its_stamp(fake_stt_install, monkeypatch):
    # T-25's acceptance is "--yes removes both". Every target must exist
    # going in, or a regression that removes only targets[0] (or stops
    # after the first rmtree) would still pass this test. Since run 7 the
    # command removes what it built by name (the bundle, its stamp, and a
    # crash-era `.recorder-build-*` / `.recorder-old-*` leftover), never
    # the bin dir wholesale, so a stray neighbour survives (T-61). The
    # menu-bar app itself lives under ~/Library/Application Support, not
    # here (design.md § LaunchAgent and bundle identity).
    CliRunner().invoke(main, ["local", "install", "--stt", "--yes"])
    bin_dir = stt_manifest.MODEL_DIR.parent.parent / "bin"
    monkeypatch.setattr(install_module, "BIN_DIR", bin_dir)
    bundle = install_module.recorder_bundle()
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "recorder").write_text("x")
    stamp = install_module.recorder_stamp_path()
    stamp.write_text("{}")
    neighbour = bin_dir / "Something Else.app"
    neighbour.mkdir()
    (bin_dir / ".else").write_text("{}")
    leftover = bin_dir / ".recorder-old-4242.app"
    leftover.mkdir()

    plan = CliRunner().invoke(main, ["local", "uninstall", "--stt"], input="n\n")
    assert f"Whisper {stt_manifest.DEFAULT_MODEL}" in plan.output
    assert "Parakeet" in plan.output
    assert str(bundle) in plan.output
    assert str(stamp) in plan.output
    assert str(leftover) in plan.output
    assert str(neighbour) not in plan.output

    result = CliRunner().invoke(main, ["local", "uninstall", "--stt", "--yes"])

    assert result.exit_code == 0
    assert not bundle.exists()
    assert not stamp.exists()
    assert not stt_manifest.MODEL_DIR.exists()
    assert not leftover.exists()
    assert neighbour.is_dir() and (bin_dir / ".else").is_file()


def test_uninstall_through_a_symlinked_model_folder_removes_only_model_files(fake_stt_install):
    # A user who pointed the model folder at an external disk: the files go
    # by manifest name, as the settings page's button removes them, and
    # nothing else in that folder (nor the folder or the link) is touched.
    real_target = stt_manifest.MODEL_DIR.parent / "elsewhere"
    real_target.mkdir(parents=True)
    (real_target / "keep.bin").write_text("x")
    stt_manifest.MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
    stt_manifest.MODEL_DIR.symlink_to(real_target)

    result = CliRunner().invoke(main, ["local", "uninstall", "--stt", "--yes"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "Nothing to remove."
    assert (real_target / "keep.bin").exists()
    assert stt_manifest.MODEL_DIR.is_symlink()


def test_uninstall_without_a_flag_removes_kokoro_and_its_environment(fake_install):
    CliRunner().invoke(main, ["local", "install", "--yes"])
    envs = list(locks.envs_dir().glob("kokoro-*"))
    assert envs and all((manifest.MODEL_DIR / e["name"]).exists() for e in manifest.FILES)

    declined = CliRunner().invoke(main, ["local", "uninstall"], input="n\n")
    assert declined.exit_code == 1 and "Kokoro" in declined.output
    assert all((manifest.MODEL_DIR / e["name"]).exists() for e in manifest.FILES)

    result = CliRunner().invoke(main, ["local", "uninstall", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Kokoro is removed." in result.output
    assert not any((manifest.MODEL_DIR / e["name"]).exists() for e in manifest.FILES)
    assert not any(path.exists() for path in envs)
    again = CliRunner().invoke(main, ["local", "uninstall", "--yes"])
    assert again.output.strip() == "Nothing to remove."


def _action_calls(monkeypatch):
    from vocalize import actions

    calls = []

    def run(action_id, params, progress=None):
        calls.append((action_id, dict(params)))
        return f"ran {action_id}"

    monkeypatch.setattr(actions, "run", run)
    return calls


@pytest.mark.parametrize("args, expected", [
    ([], [("uninstall_kokoro", {})]),
    (["--piper"], [("uninstall_piper", {})]),
    (["--llm"], [("uninstall_cleanup", {})]),
    (["--stt", "--engine", "parakeet"], [("uninstall_parakeet", {})]),
    (["--stt", "--model", "small.en"], [("uninstall_whisper", {"model": "small.en"})]),
    (["--stt", "--engine", "whisper", "--model", "base.en"],
     [("uninstall_whisper", {"model": "base.en"})]),
])
def test_each_uninstall_choice_runs_the_settings_page_action(monkeypatch, tmp_path, args, expected):
    calls = _action_calls(monkeypatch)
    monkeypatch.setattr("vocalize.cli._anything_to_uninstall", lambda steps: True)

    result = CliRunner().invoke(main, ["local", "uninstall", *args, "--yes"])

    assert result.exit_code == 0, result.output
    assert calls == expected
    for action_id, _ in expected:
        assert f"ran {action_id}" in result.output


def test_uninstall_stt_alone_removes_both_engines_every_whisper_model_and_the_recorder(
        fake_stt_install, monkeypatch):
    for model in ("base.en", "small.en"):
        entry = stt_manifest.file_for(model)
        stt_manifest.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        (stt_manifest.MODEL_DIR / entry["name"]).write_bytes(b"x")
    calls = _action_calls(monkeypatch)

    result = CliRunner().invoke(main, ["local", "uninstall", "--stt", "--yes"])

    assert result.exit_code == 0, result.output
    assert calls == [("uninstall_parakeet", {}), ("uninstall_whisper", {"model": "base.en"}),
                     ("uninstall_whisper", {"model": "small.en"})]
    assert "Speech-to-text uninstalled." in result.output


def test_an_engine_choice_keeps_the_recorder(fake_stt_install, monkeypatch):
    bin_dir = stt_manifest.MODEL_DIR.parent.parent / "bin"
    monkeypatch.setattr(install_module, "BIN_DIR", bin_dir)
    bundle = install_module.recorder_bundle()
    bundle.mkdir(parents=True)
    _action_calls(monkeypatch)
    monkeypatch.setattr("vocalize.cli._anything_to_uninstall", lambda steps: True)

    result = CliRunner().invoke(main, ["local", "uninstall", "--stt", "--engine", "whisper", "--yes"])

    assert result.exit_code == 0, result.output
    assert bundle.is_dir()


@pytest.mark.parametrize("args, message", [
    (["--stt", "--llm"], "mutually exclusive"),
    (["--piper", "--stt"], "mutually exclusive"),
    (["--engine", "whisper"], "only apply together with --stt"),
    (["--model", "base.en"], "only apply together with --stt"),
    (["--stt", "--engine", "parakeet", "--model", "base.en"], "--engine whisper"),
    (["--stt", "--model", "../../etc"], "Unknown Whisper model"),
])
def test_uninstall_refuses_a_choice_that_does_not_fit(monkeypatch, args, message):
    calls = _action_calls(monkeypatch)
    result = CliRunner().invoke(main, ["local", "uninstall", *args, "--yes"])
    assert result.exit_code != 0
    assert message in result.output
    assert calls == []


def test_an_action_error_is_a_clean_message(monkeypatch):
    from vocalize import actions

    def refuse(action_id, params, progress=None):
        raise actions.ActionError("Could not remove a file: denied")

    monkeypatch.setattr(actions, "run", refuse)
    monkeypatch.setattr("vocalize.cli._anything_to_uninstall", lambda steps: True)
    result = CliRunner().invoke(main, ["local", "uninstall", "--piper", "--yes"])
    assert result.exit_code != 0
    assert "Could not remove a file: denied" in result.output
    assert "Traceback" not in result.output


def test_a_directory_that_cannot_be_created_is_an_install_error(monkeypatch, tmp_path):
    """Every other failure in `download_file` is an InstallError, not a traceback.

    The mkdir sat *above* the try, so a full disk or an unwritable
    `~/.cache` reached the user as a raw OSError (DEC-014 / review).
    """
    def refuse(path):
        raise OSError("Read-only file system")

    monkeypatch.setattr(install_module, "ensure_private_dir", refuse)

    with pytest.raises(install_module.InstallError):
        install_module.download_file("https://example.invalid/x", tmp_path / "sub" / "x", 1, "ab")


def test_the_cache_directory_is_tightened_even_when_it_already_exists(tmp_path):
    """`mkdir(exist_ok=True, mode=0o700)` never touches an existing directory.

    Every machine that had `~/.cache/vocalize` before the mode was added
    kept 0755, so its listing said a dictation was in progress.
    """
    existing = tmp_path / "cache"
    existing.mkdir(mode=0o755)

    install_module.ensure_private_dir(existing)

    assert stat.S_IMODE(existing.stat().st_mode) == 0o700


# --- local install / uninstall --llm (T-132) -----------------------------


def test_local_install_llm_stt_mutually_exclusive():
    result = CliRunner().invoke(main, ["local", "install", "--stt", "--llm"])
    assert result.exit_code != 0
    assert "--stt and --llm are mutually exclusive" in result.output


def test_local_install_llm_ram_gate_and_force(monkeypatch, tmp_path):
    from vocalize import local as local_module
    from vocalize.local import llm_manifest

    monkeypatch.setattr(local_module, "physical_ram_bytes", lambda: 8 * 1024**3)
    monkeypatch.setattr(llm_manifest, "MODEL_DIR", tmp_path / "models" / "qwen")
    # uv is checked before the RAM gate; a CI runner without uv must still
    # reach the gate this test is about.
    monkeypatch.setattr(local_pkg, "uv_path", lambda: "/usr/bin/true")

    # Without --force: fails at RAM gate
    result = CliRunner().invoke(main, ["local", "install", "--llm", "--yes"])
    assert result.exit_code != 0
    assert "8.0 GB of RAM" in result.output
    assert "at least 12.0 GB" in result.output
    assert 'cleanup = "off"' in result.output
    assert "claude-cli" not in result.output

    # With --force: passes the RAM gate
    # Fake download and selftest
    monkeypatch.setattr(install_module, "download_file", lambda *a, **kw: None)
    monkeypatch.setattr(install_module, "file_is_verified", lambda *a, **kw: True)
    monkeypatch.setattr(install_module, "selftest", lambda *a, **kw: None)
    monkeypatch.setattr(local_pkg, "uv_path", lambda: "/usr/bin/true")
    monkeypatch.setattr(llm_manifest, "check_config", lambda *a, **kw: None)

    result_force = CliRunner().invoke(main, ["local", "install", "--llm", "--force", "--yes"])
    assert result_force.exit_code == 0
    assert "--force: skipping RAM check" in result_force.output


def test_local_install_llm_and_uninstall_lifecycle(monkeypatch, tmp_path):
    from vocalize import local as local_module
    from vocalize.local import llm_manifest

    model_dir = tmp_path / "models" / "qwen"
    monkeypatch.setattr(llm_manifest, "MODEL_DIR", model_dir)
    monkeypatch.setattr(local_module, "physical_ram_bytes", lambda: 16 * 1024**3)
    monkeypatch.setattr(local_pkg, "uv_path", lambda: "/usr/bin/true")
    monkeypatch.setattr(llm_manifest, "check_config", lambda *a, **kw: None)

    # Fake download: creates files and writes stamp
    def _fake_download(url, dest, size, sha, **kw):
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            f.truncate(size)

    monkeypatch.setattr(install_module, "download_file", _fake_download)
    monkeypatch.setattr(install_module, "selftest",
                        _locked_selftest(lambda uv, kw: None, llm_manifest))

    # Install
    res = CliRunner().invoke(main, ["local", "install", "--llm", "--yes"])
    assert res.exit_code == 0
    assert "LLM is ready." in res.output or "The language model runs entirely on this machine" in res.output

    # Status shows LLM ready
    res_status = CliRunner().invoke(main, ["local", "status"])
    assert "LLM: ready" in res_status.output

    # Uninstall
    res_un = CliRunner().invoke(main, ["local", "uninstall", "--llm", "--yes"])
    assert res_un.exit_code == 0
    assert "Language model uninstalled." in res_un.output
    assert not model_dir.exists()


# --- what the install and status screens claim (M2, M3) ---------------------


def test_the_install_plan_does_not_overstate_what_is_checked(fake_install, tiny_manifest):
    result = CliRunner().invoke(main, ["local", "install"], input="n\n")

    assert "from PyPI" not in result.output
    assert "pinned address" in result.output
    assert "redirect must stay on HTTPS" in result.output
    assert "every model file is checked against its sha256 and size" in result.output
    assert f"hash-locked list {manifest.lock_file().name}" in result.output


def test_the_piper_plan_says_its_packages_are_not_hash_locked(monkeypatch):
    from vocalize.local import piper_manifest

    monkeypatch.setattr(local_pkg, "uv_path", lambda: "/usr/local/bin/uv")
    result = CliRunner().invoke(main, ["local", "install", "--piper"], input="n\n")

    assert result.exit_code == 1
    assert "from PyPI" not in result.output
    assert "hash-locked list" not in result.output
    assert f"{piper_manifest.RUNTIME_PACKAGE}, pinned by version only" in result.output
    assert "every model file is checked against its sha256 and size" in result.output


def test_status_says_a_missing_locked_runtime_is_not_installed(tiny_stt_manifest):
    from vocalize.local import llm_manifest

    result = CliRunner().invoke(main, ["local", "status"])

    assert "via uv" not in result.output
    assert "installed from the hash-locked list" not in result.output
    assert (f"not installed yet (hash-locked list {llm_manifest.lock_file().name}) — run: "
            "vocalize local install --llm") in result.output
    assert f"not installed yet (hash-locked list {stt_manifest.lock_file().name})" in result.output


def test_status_names_the_hash_locked_list_once_it_is_installed(tiny_stt_manifest):
    from vocalize.local import llm_manifest

    for m in (stt_manifest, llm_manifest):
        python = locks.env_python(m.ENGINE, m.lock_file())
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")
        locks.write_marker(m.ENGINE, m.lock_file())

    result = CliRunner().invoke(main, ["local", "status"])

    assert f"installed from the hash-locked list {stt_manifest.lock_file().name}" in result.output
    assert f"installed from the hash-locked list {llm_manifest.lock_file().name}" in result.output
    assert "not installed yet" not in result.output


def test_local_status_ends_with_the_upgrade_line(tiny_stt_manifest, monkeypatch):
    from vocalize import readiness

    monkeypatch.setattr("vocalize.cli.upgrade_commands",
                        lambda: ["vocalize local install", "vocalize local install --llm"])
    result = CliRunner().invoke(main, ["local", "status"])

    assert result.output.rstrip().splitlines()[-1] == readiness.upgrade_line(
        ["vocalize local install", "vocalize local install --llm"])
    monkeypatch.setattr("vocalize.cli.upgrade_commands", list)
    assert "After upgrading" not in CliRunner().invoke(main, ["local", "status"]).output


def test_an_upgraded_kokoro_is_named_in_the_upgrade_line(fake_install, monkeypatch):
    """What 0.15 leaves: verified model files and stamp, no locked environment."""
    from vocalize import readiness

    CliRunner().invoke(main, ["local", "install", "--yes"])
    for path in locks.envs_dir().glob("kokoro-*"):
        import shutil
        shutil.rmtree(path)

    assert "vocalize local install" in readiness.upgrade_commands()
    result = CliRunner().invoke(main, ["local", "status"])
    last = result.output.rstrip().splitlines()[-1]
    assert last.startswith("After upgrading from an older version, run once: vocalize local install")


# --- the install's own commands get an allow-listed environment (M4) ---------

NETWORK_ENV_NAMES = {
    "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "UV_CACHE_DIR", "XDG_CACHE_HOME",
    "UV_PYTHON_INSTALL_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY",
    "https_proxy", "http_proxy", "no_proxy", "all_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
}
INSTALL_CANARIES = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN", "GITHUB_TOKEN", "AWS_SECRET",
    "CLIENT_SECRET", "DB_PASSWORD", "PYTHONPATH", "PYTHONSTARTUP", "UV_INDEX_URL",
    "UV_EXTRA_INDEX_URL", "PIP_INDEX_URL", "VIRTUAL_ENV", "HF_ENDPOINT",
)


@pytest.fixture
def install_canaries(monkeypatch):
    for name in INSTALL_CANARIES:
        monkeypatch.setenv(name, "CREDENTIAL_CANARY")
    for name in ("HTTPS_PROXY", "no_proxy", "SSL_CERT_FILE", "UV_PYTHON_INSTALL_DIR"):
        monkeypatch.setenv(name, f"/kept/{name}")


def _assert_network_env(env):
    assert env is not None, "an install command must never inherit the caller's environment"
    assert set(env) <= NETWORK_ENV_NAMES
    assert "CREDENTIAL_CANARY" not in env.values()
    for name in ("HTTPS_PROXY", "no_proxy", "SSL_CERT_FILE", "UV_PYTHON_INSTALL_DIR"):
        assert env[name] == f"/kept/{name}"


def test_the_network_steps_get_only_the_allow_listed_variables(install_canaries, tiny_manifest):
    from vocalize.local import runtime

    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        _make_venv(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    install_module.selftest("/usr/local/bin/uv", runner=runner)

    (venv, venv_kw), (sync, sync_kw), (run, run_kw) = calls
    assert venv[1] == "venv" and sync[1:3] == ["pip", "sync"] and run[1] == "run"
    _assert_network_env(venv_kw.get("env"))
    _assert_network_env(sync_kw.get("env"))
    # The selftest itself is offline: exactly the workers' environment.
    assert run_kw.get("env") == runtime.environment()
    assert "CREDENTIAL_CANARY" not in run_kw["env"].values()


def test_the_unlocked_piper_selftest_gets_the_network_list_with_the_hub_off(
    install_canaries, tmp_path,
):
    from vocalize.local import piper_manifest

    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    install_module.selftest("/uv", manifest=piper_manifest, model_dir=tmp_path, runner=runner)

    ((_, kwargs),) = calls
    env = dict(kwargs.get("env"))
    assert env.pop("HF_HUB_OFFLINE") == env.pop("TRANSFORMERS_OFFLINE") == "1"
    assert env.pop("HF_HUB_DISABLE_TELEMETRY") == "1"
    _assert_network_env(env)


def test_the_network_environment_is_exactly_the_allow_list(monkeypatch):
    for name in NETWORK_ENV_NAMES:
        monkeypatch.setenv(name, "v")
    for name in INSTALL_CANARIES:
        monkeypatch.setenv(name, "CREDENTIAL_CANARY")

    assert set(install_module.network_environment()) == NETWORK_ENV_NAMES
