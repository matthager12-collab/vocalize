"""Download, verify and stamp Kokoro's model files.

Kept apart from the CLI so the command stays a thin shell around it, and
so the tests can drive a download with a fake `opener` and never touch
the network.

The rules that make a 326 MB download from GitHub acceptable:

* HTTPS only, at a URL pinned to one release tag.
* The bytes are hashed while they stream, and both the size and the
  sha256 must match the manifest before anything is renamed into place.
* A failed file is deleted, not left half-written under its real name —
  which is why every download lands on `<name>.part` first.
* The `.verified` stamp is written last, only once both files passed.
  `check()` trusts the stamp, so an interrupted install cannot look done.
* Nothing downloaded is executed or unpickled. The ONNX weights are read
  by onnxruntime inside the uv worker; the voice pack is a numpy array.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..audio import ensure_private_dir
from . import kokoro_manifest as manifest

# Big enough that a 326 MB file isn't a million iterations, small enough
# to keep progress reporting responsive.
_BLOCK = 1024 * 1024

_DOWNLOAD_TIMEOUT = 60
_SELFTEST_TIMEOUT = 900


class InstallError(Exception):
    """Something went wrong installing the local runtime."""


class _HttpsOnlyRedirects(HTTPRedirectHandler):
    """Follow redirects (GitHub release assets legitimately hop to
    objects.githubusercontent.com), but refuse an https-to-http downgrade.
    """

    def redirect_request(
        self, req: Request, fp, code: int, msg: str, headers, newurl: str
    ) -> Request:
        if not newurl.startswith("https://"):
            raise HTTPError(newurl, code, "refusing an insecure redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Built once at import time; the `opener=` parameter still lets tests
# swap in a fake that never touches the network.
_default_opener = build_opener(_HttpsOnlyRedirects()).open


def _model_dir(model_dir: Path | None, manifest=manifest) -> Path:
    return manifest.MODEL_DIR if model_dir is None else model_dir


def download_file(
    url: str,
    dest: Path,
    expected_size: int,
    expected_sha256: str,
    opener=_default_opener,
    progress=None,
) -> Path:
    """Stream `url` to `dest`, verifying size and sha256 as it goes.

    Writes `<dest>.part` and only renames it over `dest` once both checks
    pass. Raises InstallError (having removed the part file) otherwise.
    """
    if not url.startswith("https://"):
        raise InstallError(f"Refusing to download over an insecure URL: {url}")

    try:
        ensure_private_dir(dest.parent)
    except OSError as exc:
        # Every other failure in this function is an InstallError with a
        # message; a full disk or an unwritable ~/.cache must not be the
        # one that reaches the user as a traceback.
        raise InstallError(f"Could not create {dest.parent}: {exc}") from exc
    part = dest.with_name(dest.name + ".part")

    digest = hashlib.sha256()
    written = 0
    try:
        try:
            with opener(url, timeout=_DOWNLOAD_TIMEOUT) as response, part.open("wb") as out:
                while True:
                    block = response.read(_BLOCK)
                    if not block:
                        break
                    written += len(block)
                    if written > expected_size:
                        raise InstallError(
                            f"{dest.name} is larger than expected "
                            f"({expected_size} bytes); nothing was installed"
                        )
                    digest.update(block)
                    out.write(block)
                    if progress is not None:
                        progress(written, expected_size)
        except (URLError, OSError, http.client.HTTPException) as exc:
            raise InstallError(f"Could not download {dest.name}: {exc}") from exc
    except BaseException:
        # Covers our own "too large" raise above and anything else,
        # Ctrl-C included: never leave a partial file lying around.
        part.unlink(missing_ok=True)
        raise

    if written != expected_size:
        part.unlink(missing_ok=True)
        raise InstallError(
            f"{dest.name} is the wrong size ({written} bytes, expected "
            f"{expected_size}). Nothing was installed."
        )

    actual = digest.hexdigest()
    if actual != expected_sha256:
        part.unlink(missing_ok=True)
        raise InstallError(
            f"{dest.name} failed its checksum (got {actual}, expected "
            f"{expected_sha256}). The file was deleted and nothing was installed."
        )

    os.replace(part, dest)
    return dest


def file_is_verified(entry: dict, model_dir: Path | None = None, manifest=manifest) -> bool:
    """Whether `entry`'s file is already on disk with the right size and
    sha256, so a partial install can skip re-downloading it.

    Hashing is a one-time 1-2s cost per file, well worth it against a
    326 MB re-download.
    """
    path = _model_dir(model_dir, manifest) / entry["name"]
    try:
        if path.stat().st_size != entry["size"]:
            return False
    except OSError:
        return False

    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(_BLOCK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest() == entry["sha256"]


def stamp_path(model_dir: Path | None = None, manifest=manifest) -> Path:
    return _model_dir(model_dir, manifest) / manifest.STAMP_NAME


def stamp_files(stamp: dict | None, manifest=manifest) -> dict:
    """The per-file records in `stamp`, or `{}` if there is nothing to trust.

    A `.verified` file is only ever written by `write_stamp()` below, but
    it can still be read back corrupted — hand-edited, truncated, or left
    over from a foreign tool — so this is the one place that turns an
    untrusted stamp into a dict every caller can index safely: a stamp
    from an older `manifest_version`, a `files` value that isn't a dict,
    or an individual entry that isn't a dict, are all treated the same as
    "nothing recorded" rather than raising in `write_stamp()`'s merge,
    `installed()`'s comparison, or `local status`'s per-file line.
    """
    stamp = stamp or {}
    if stamp.get("manifest_version") != manifest.MANIFEST_VERSION:
        return {}
    files = stamp.get("files")
    if not isinstance(files, dict):
        return {}
    return {name: entry for name, entry in files.items() if isinstance(entry, dict)}


def write_stamp(model_dir: Path | None = None, manifest=manifest, files=None) -> Path:
    """Record what was verified. Written last, and only after every file in
    `files` (default: every entry in `manifest.FILES`) has passed.

    A manifest that downloads one entry per install (Whisper: only the
    selected model) passes `files=[that entry]`. The stamp then covers
    only what was actually downloaded — but a *different* file verified
    by an earlier call is not forgotten: the previous stamp's entries are
    kept unless this manifest's version has changed, so installing a
    second model later does not un-verify the first one. Kokoro always
    passes every entry every time, so its stamp's shape never changes —
    this is what keeps it byte-identical to before this function grew a
    `files=` parameter.
    """
    path = stamp_path(model_dir, manifest)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    entries = manifest.FILES if files is None else files

    previous = read_stamp(model_dir, manifest)
    recorded = stamp_files(previous, manifest)
    for entry in entries:
        recorded[entry["name"]] = {"size": entry["size"], "sha256": entry["sha256"]}

    path.write_text(
        json.dumps(
            {"manifest_version": manifest.MANIFEST_VERSION, "files": recorded},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def read_stamp(model_dir: Path | None = None, manifest=manifest) -> dict | None:
    """The stamp, or None when it is missing or unreadable garbage."""
    try:
        data = json.loads(stamp_path(model_dir, manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def installed(
    manifest, model_dir: Path | None = None, files=None, install_hint="vocalize local install"
) -> tuple[bool, str]:
    """(ready, reason) for every entry in `files` (default: all of
    `manifest.FILES`) — used directly by the STT commands, and by
    Kokoro's own `installed()` wrapper in providers/kokoro.py.

    Every entry must exist on disk at the manifest's exact size, and the
    stamp left behind by a verified install must agree on each one's
    sha256. Sizes are checked, hashes are not re-computed here — hashing
    hundreds of MB per call would cost more than the work it is gating;
    the stamp is what makes that safe to trust.
    """
    base = _model_dir(model_dir, manifest)
    entries = manifest.FILES if files is None else files

    for entry in entries:
        path = base / entry["name"]
        try:
            size = path.stat().st_size
        except OSError:
            return False, f"not installed — run: {install_hint}"
        if size != entry["size"]:
            return False, f"{entry['name']} is the wrong size — reinstall with: {install_hint}"

    stamp = read_stamp(model_dir, manifest)
    if stamp is None:
        return False, f"not verified — run: {install_hint}"
    if stamp.get("manifest_version") != manifest.MANIFEST_VERSION:
        return False, f"installed by an older vocalize — run: {install_hint}"

    recorded = stamp_files(stamp, manifest)
    for entry in entries:
        seen = recorded.get(entry["name"]) or {}
        if (seen.get("sha256"), seen.get("size")) != (entry["sha256"], entry["size"]):
            return False, f"{entry['name']} does not match this release — run: {install_hint}"

    return True, ""


def selftest(
    uv: str, manifest=manifest, model_dir: Path | None = None, runner=subprocess.run,
    **manifest_kwargs,
) -> str:
    """Warm the uv environment by loading the model and saying one word.

    The argv is the manifest's own business (a voice and a language mean
    nothing here, and Whisper's selftest needs to know which model was
    selected) — `manifest.selftest_argv(model_dir, **manifest_kwargs)`
    returns everything after the `uv` executable, and this function only
    runs it: always `--no-project`, never the caller's own directory.
    """
    base = _model_dir(model_dir, manifest)
    argv = [uv, *manifest.selftest_argv(base, **manifest_kwargs)]
    try:
        result = runner(
            argv, capture_output=True, text=True, timeout=_SELFTEST_TIMEOUT, check=False,
            cwd=tempfile.gettempdir(),  # never the caller's project dir
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(f"Could not run the runtime: {exc}") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip().splitlines()
        raise InstallError(stderr[-1] if stderr else "the runtime failed to start")
    return (result.stdout or "").strip()


# --- the recorder bundle ----------------------------------------------
#
# macOS grants the microphone to a bundle with a usage string, never to a
# script (design.md § Recorder contract, DEC-001). So `local install --stt`
# compiles vocalize/recorder/VocalizeRecorder.swift into an ad-hoc signed
# "Vocalize Recorder.app" the user can see — and grant once — in
# System Settings › Privacy & Security › Microphone.
#
# Ad-hoc signing ties that grant to the bytes: a rebuild is a new identity
# and the user has to grant it again. That is why the build is content-
# addressed — the stamp holds the source and template hashes, and an
# unchanged source is never recompiled — and why a real rebuild is
# announced rather than done quietly.

_RECORDER_DIR = Path(__file__).resolve().parent.parent / "recorder"
RECORDER_SOURCE = _RECORDER_DIR / "VocalizeRecorder.swift"
RECORDER_PLIST_TEMPLATE = _RECORDER_DIR / "Info.plist.in"

BIN_DIR = Path.home() / ".cache" / "vocalize" / "bin"
BUNDLE_NAME = "Vocalize Recorder.app"
RECORDER_STAMP_NAME = ".recorder"
RECORDER_STAMP_VERSION = 2

_BUILD_TIMEOUT = 600

_CLT_HINT = (
    "The Swift compiler is missing, and the recorder is built from source on "
    "this machine. Install Apple's Command Line Tools with: xcode-select --install"
)
_LICENSE_HINT = (
    "The Xcode / Command Line Tools licence has not been accepted, so the Swift "
    "compiler refuses to run. Accept it with: sudo xcodebuild -license accept"
)


def recorder_bundle(bin_dir: Path | None = None) -> Path:
    return (BIN_DIR if bin_dir is None else bin_dir) / BUNDLE_NAME


def recorder_binary(bin_dir: Path | None = None) -> Path:
    return recorder_bundle(bin_dir) / "Contents" / "MacOS" / "recorder"


def recorder_stamp_path(bin_dir: Path | None = None) -> Path:
    return (BIN_DIR if bin_dir is None else bin_dir) / RECORDER_STAMP_NAME


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(_BLOCK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _recorder_fingerprint() -> dict:
    """What the bundle was built from: change any of it and the ad-hoc
    signature changes, so the microphone grant has to be given again.

    The vocalize version is deliberately absent. The bundle's signature is
    its TCC identity, and a fingerprint that moved with every release would
    rebuild a byte-identical recorder and cost the user a re-grant on every
    upgrade (DEC-010).
    """
    return {
        "stamp_version": RECORDER_STAMP_VERSION,
        "source_sha256": _sha256_of(RECORDER_SOURCE),
        "plist_sha256": _sha256_of(RECORDER_PLIST_TEMPLATE),
    }


def _stamp_is_current(stamp, fingerprint: dict, binary: Path) -> bool:
    """True only when the stamp matches the shipped source *and* still
    describes the binary on disk.

    Every other artifact in this module is sha256-verified against its stamp
    before it is trusted; the recorder is the one this machine executes, so
    anything that can write to the cache directory must not be able to swap
    the binary and keep the stamp's blessing.
    """
    if not isinstance(stamp, dict):
        return False
    if any(stamp.get(key) != value for key, value in fingerprint.items()):
        return False
    recorded = stamp.get("binary_sha256")
    if not isinstance(recorded, str) or not binary.is_file():
        return False
    return recorded == _sha256_of(binary)


def write_recorder_stamp(bin_dir: Path | None = None) -> Path:
    """Record what the bundle was built from, and which binary that made.

    Written last, over a finished bundle, exactly like the model
    manifests' `.verified` stamp — and read back by
    `recorder_is_current()` before anything launches the binary.
    """
    path = recorder_stamp_path(bin_dir)
    path.write_text(
        json.dumps(
            {**_recorder_fingerprint(), "binary_sha256": _sha256_of(recorder_binary(bin_dir))},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def recorder_is_current(bin_dir: Path | None = None) -> bool:
    """Whether the built recorder is the one this install of vocalize signed.

    `_stamp_is_current` says why the binary is hashed at all: this is the
    one artifact the machine *executes*, so anything that can write to the
    cache directory must not be able to swap it and keep the stamp's
    blessing. That was only ever checked at build time; every dictation
    and every `listen --check` launched whatever was at the path (DEC-014
    / review). macOS refusing to run a bundle whose binary no longer
    matches its ad-hoc signature is what has actually been containing
    this — the check belongs where the launch is, not only where the
    build is. A sha256 of 133 KB is sub-millisecond.
    """
    try:
        return _stamp_is_current(
            read_recorder_stamp(bin_dir),
            _recorder_fingerprint(),
            recorder_binary(bin_dir),
        )
    except OSError:
        return False


def read_recorder_stamp(bin_dir: Path | None = None) -> dict | None:
    """The recorder stamp, or None when it is missing or unreadable garbage."""
    try:
        data = json.loads(recorder_stamp_path(bin_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _compiler_argv(compiler) -> list[str]:
    """`xcrun swiftc` by default; a string or a list lets a test point the
    build at a fake compiler that never touches the real toolchain."""
    if compiler is None:
        return ["xcrun", "swiftc"]
    if isinstance(compiler, (str, Path)):
        return [str(compiler)]
    return [str(part) for part in compiler]


def _diagnose_compiler(output: str, what: str) -> str:
    """Turn a build failure into the one command that fixes it.

    Two failures are not the developer's Swift going wrong and must not be
    reported as a compiler error: the Command Line Tools are not installed
    at all, and they are installed but their licence was never accepted.
    """
    lowered = output.lower()
    if "license" in lowered and ("xcodebuild" in lowered or "xcode" in lowered):
        return _LICENSE_HINT
    if (
        "unable to find utility" in lowered
        or "no developer tools" in lowered
        or "xcode-select" in lowered
        or "command line tools" in lowered
    ):
        return _CLT_HINT
    lines = [line for line in output.strip().splitlines() if line.strip()]
    detail = lines[-1] if lines else "no output"
    return f"The recorder bundle could not be {what}: {detail}"


def _run_build_step(argv: list[str], runner, missing_hint: str, what: str) -> None:
    try:
        result = runner(
            argv, capture_output=True, text=True, timeout=_BUILD_TIMEOUT, check=False,
            cwd=tempfile.gettempdir(),  # never the caller's project dir
        )
    except FileNotFoundError as exc:
        raise InstallError(missing_hint) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(f"The recorder bundle could not be {what}: {exc}") from exc
    if result.returncode != 0:
        raise InstallError(
            _diagnose_compiler(f"{result.stdout or ''}\n{result.stderr or ''}", what)
        )


def _swap_in(staging: Path, bundle: Path) -> None:
    """Put the finished bundle where the granted one was, as one rename.

    The bundle the user granted the microphone to must never be left holding
    a new binary under an old signature: macOS kills a binary whose signature
    no longer validates, which surfaces as an unexplained SIGKILL long after
    the install said nothing.
    """
    previous = bundle.with_name(f".recorder-old-{os.getpid()}.app")
    shutil.rmtree(previous, ignore_errors=True)
    if bundle.exists():
        os.replace(bundle, previous)
    try:
        os.replace(staging, bundle)
    except OSError:
        if previous.exists():
            os.replace(previous, bundle)
        raise
    shutil.rmtree(previous, ignore_errors=True)


def build_recorder(
    bin_dir: Path | None = None, compiler=None, runner=subprocess.run,
) -> tuple[str, Path]:
    """Build (or leave alone) the recorder bundle. Returns (status, bundle).

    status is "current" when the bundle already matches the shipped source,
    "built" the first time, and "rebuilt" when a bundle was there and had to
    be replaced — the one case the caller must warn about, because the new
    ad-hoc signature is a new identity and macOS has forgotten the grant.

    `compiler` and `runner` exist so the tests can drive the whole build
    through a fake compiler: nothing here should ever need Xcode to be
    installed on the machine running the suite.
    """
    if not RECORDER_SOURCE.is_file() or not RECORDER_PLIST_TEMPLATE.is_file():
        raise InstallError(
            "This install of vocalize is missing the recorder source "
            f"({RECORDER_SOURCE.name}); reinstall vocalize to get it back."
        )

    bundle = recorder_bundle(bin_dir)
    binary = recorder_binary(bin_dir)
    plist = bundle / "Contents" / "Info.plist"
    fingerprint = _recorder_fingerprint()
    stamp = read_recorder_stamp(bin_dir)

    if _stamp_is_current(stamp, fingerprint, binary) and plist.is_file():
        return "current", bundle

    # Decided from the artifact, not the stamp: a bundle on disk was granted
    # the microphone under the old signature whether or not its stamp
    # survived, and a truncated stamp beside an intact bundle is still a
    # re-grant the user has to be told about.
    status = "rebuilt" if (stamp is not None or bundle.exists()) else "built"

    ensure_private_dir(bundle.parent)
    staging = bundle.with_name(f".recorder-build-{os.getpid()}.app")
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staged_binary = staging / "Contents" / "MacOS" / "recorder"
        staged_binary.parent.mkdir(parents=True, mode=0o700)
        _run_build_step(
            [
                *_compiler_argv(compiler), "-O",
                "-framework", "AVFoundation", "-framework", "CoreAudio",
                "-o", str(staged_binary), str(RECORDER_SOURCE),
            ],
            runner, _CLT_HINT, "compiled",
        )

        (staging / "Contents" / "Info.plist").write_text(
            RECORDER_PLIST_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8",
        )

        # Last, and over the finished bundle: the signature has to cover the
        # Info.plist that carries the identity and the usage string.
        # --options runtime is the hardened runtime, which under the ad-hoc
        # signature refuses DYLD_INSERT_LIBRARIES — so nothing can inject a
        # dylib into the one process on this machine holding a microphone
        # grant. It does *not* stop that bundle being launched directly:
        # the grant belongs to the bundle, and anything running as this user
        # can `open` it with its own --out and record. That is inherent to
        # DEC-001 and is stated in docs/dictation.md § Privacy; there is no
        # code mitigation, only `local uninstall --stt` plus revoking the
        # grant in System Settings.
        _run_build_step(
            ["codesign", "-s", "-", "--force", "--options", "runtime", str(staging)],
            runner, _CLT_HINT, "signed",
        )
        _swap_in(staging, bundle)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    write_recorder_stamp(bin_dir)
    return status, bundle
