"""`vocalize local install` — the download, the verification, the command.

Every test runs against a fake opener and a tiny two-file manifest, so
the suite never downloads 354 MB, never reaches the network, and never
writes outside tmp_path.
"""

import hashlib
import http.client
import json
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from click.testing import CliRunner

from vocalize.cli import main
from vocalize.local import install as install_module
from vocalize.local import kokoro_manifest as manifest
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


# --- selftest ---------------------------------------------------------


def test_the_selftest_runs_the_worker_through_uv(tmp_path, tiny_manifest):
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    assert install_module.selftest("/usr/local/bin/uv", runner=runner) == "ok"

    argv, kwargs = calls[0]
    # --no-project + a neutral cwd: uv must never mistake the caller's
    # directory for a project and rebuild that project's .venv.
    assert argv[:7] == [
        "/usr/local/bin/uv", "run", "--no-project",
        "--python", manifest.PYTHON_VERSION,
        "--with", manifest.RUNTIME_PACKAGE,
    ]
    assert argv[7] == str(manifest.worker_path())
    assert argv[-1] == "--selftest"
    assert kwargs["cwd"] == tempfile.gettempdir()


def test_a_failing_selftest_reports_the_last_stderr_line(tiny_manifest):
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="warming up\nkokoro: could not load the model\n"
        )

    with pytest.raises(install_module.InstallError, match="could not load the model"):
        install_module.selftest("uv", runner=runner)


# --- the CLI ----------------------------------------------------------


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
    monkeypatch.setattr(install_module, "selftest", lambda uv, **kw: selftests.append(uv) or "ok")
    monkeypatch.setattr(provider, "uv_path", lambda: "/usr/local/bin/uv")
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
    monkeypatch.setattr(provider, "uv_path", lambda: None)

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
