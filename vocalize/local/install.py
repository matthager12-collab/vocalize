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
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

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


def _model_dir(model_dir: Path | None) -> Path:
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

    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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


def file_is_verified(entry: dict, model_dir: Path | None = None) -> bool:
    """Whether `entry`'s file is already on disk with the right size and
    sha256, so a partial install can skip re-downloading it.

    Hashing is a one-time 1-2s cost per file, well worth it against a
    326 MB re-download.
    """
    path = _model_dir(model_dir) / entry["name"]
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


def stamp_path(model_dir: Path | None = None) -> Path:
    return _model_dir(model_dir) / manifest.STAMP_NAME


def write_stamp(model_dir: Path | None = None) -> Path:
    """Record what was verified. Written last, and only after both files pass."""
    path = stamp_path(model_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
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
        + "\n",
        encoding="utf-8",
    )
    return path


def read_stamp(model_dir: Path | None = None) -> dict | None:
    """The stamp, or None when it is missing or unreadable garbage."""
    try:
        data = json.loads(stamp_path(model_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def selftest(uv: str, model_dir: Path | None = None, runner=subprocess.run) -> str:
    """Warm the uv environment by loading the model and saying one word."""
    model, voices = manifest.file_paths(_model_dir(model_dir))
    # --no-project: never let uv treat the caller's cwd as a project and
    # rebuild its .venv (see providers/kokoro.py for the full note).
    argv = [
        uv, "run", "--no-project",
        "--python", manifest.PYTHON_VERSION,
        "--with", manifest.RUNTIME_PACKAGE,
        str(manifest.worker_path()),
        "--model", str(model),
        "--voices", str(voices),
        "--voice", manifest.DEFAULT_VOICE,
        "--selftest",
    ]
    try:
        result = runner(
            argv, capture_output=True, text=True, timeout=_SELFTEST_TIMEOUT, check=False,
            cwd=tempfile.gettempdir(),  # never the caller's project dir
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(f"Could not run the Kokoro runtime: {exc}") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip().splitlines()
        raise InstallError(stderr[-1] if stderr else "the Kokoro runtime failed to start")
    return (result.stdout or "").strip()
