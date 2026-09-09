"""The bundle builder's golden test (run 7, T-61).

`build_bundle(RECORDER_SPEC)` must reproduce the 0.12.0 recorder build
byte for byte: the swiftc and codesign argv, the subprocess keyword
arguments, the fingerprint keys and the stamp version. Any drift here is a
new ad-hoc signature, and every user re-grants the microphone (DEC-010).
The rest of this file fills out in run 8b with the menu-bar app's own
build; run 7 pins only the recorder.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import replace

import pytest
from conftest import FakeToolchain as _Toolchain

from vocalize.local import install as install_module
from vocalize.local.install import APP_SPEC, RECORDER_SPEC, BundleSpec, build_bundle


@pytest.fixture
def bin_dir(tmp_path):
    return tmp_path / "bin"


def test_golden_recorder_spec_is_the_0_12_0_build():
    """The spec's fields, literally: a changed name, framework or stamp
    version is a re-grant."""
    assert RECORDER_SPEC == BundleSpec(
        source=install_module.RECORDER_SOURCE,
        plist_template=install_module.RECORDER_PLIST_TEMPLATE,
        entitlements=install_module.RECORDER_ENTITLEMENTS,
        bundle_name="Vocalize Recorder.app",
        binary_name="recorder",
        frameworks=("AVFoundation", "CoreAudio"),
        stamp_name=".recorder",
        stamp_version=3,
        noun="recorder",
    )
    assert RECORDER_SPEC.source.name == "VocalizeRecorder.swift"
    assert RECORDER_SPEC.plist_template.name == "Info.plist.in"
    assert RECORDER_SPEC.entitlements.name == "Recorder.entitlements"


def test_golden_recorder_build_argv_and_stamp_are_byte_identical(bin_dir):
    toolchain = _Toolchain()

    status, bundle = build_bundle(RECORDER_SPEC, bin_dir, runner=toolchain)

    assert status == "built"
    assert bundle == bin_dir / "Vocalize Recorder.app"
    staged = bin_dir / f".recorder-build-{os.getpid()}.app"
    swiftc, sign = toolchain.calls
    assert swiftc[0] == [
        "xcrun", "swiftc", "-O",
        "-framework", "AVFoundation", "-framework", "CoreAudio",
        "-o", str(staged / "Contents" / "MacOS" / "recorder"),
        str(install_module.RECORDER_SOURCE),
    ]
    assert sign[0] == [
        "codesign", "-s", "-", "--force", "--options", "runtime",
        "--entitlements", str(install_module.RECORDER_ENTITLEMENTS),
        str(staged),
    ]
    for _, kwargs in toolchain.calls:
        assert kwargs == {
            "capture_output": True, "text": True, "timeout": 600, "check": False,
            "cwd": tempfile.gettempdir(),
        }
    stamp = json.loads((bin_dir / ".recorder").read_text(encoding="utf-8"))
    assert list(stamp) == [
        "stamp_version", "source_sha256", "plist_sha256", "entitlements_sha256", "binary_sha256",
    ]
    assert stamp["stamp_version"] == 3
    assert (bundle / "Contents" / "Info.plist").read_text(encoding="utf-8") == (
        install_module.RECORDER_PLIST_TEMPLATE.read_text(encoding="utf-8")
    )
    assert install_module.recorder_is_current(bin_dir)


def test_golden_build_recorder_is_build_bundle_of_the_recorder_spec(bin_dir):
    """The recorder's own door and the general one make the same calls."""
    first, second = _Toolchain(), _Toolchain()
    install_module.build_recorder(bin_dir, runner=first)
    other = bin_dir.parent / "other"
    build_bundle(RECORDER_SPEC, other, runner=second)

    strip = lambda calls, root: [
        [part.replace(str(root), "<bin>") for part in argv] for argv, _ in calls
    ]
    assert strip(first.calls, bin_dir) == strip(second.calls, other)


def test_a_spec_without_entitlements_signs_without_the_flag(bin_dir, tmp_path):
    """The menu-bar app asks for no device, so its signature carries no
    entitlements and its fingerprint has no entitlements key."""
    source = tmp_path / "Probe.swift"
    source.write_text("import AppKit\n", encoding="utf-8")
    plist = tmp_path / "Info.plist.in"
    plist.write_text("<plist/>\n", encoding="utf-8")
    spec = BundleSpec(
        source=source, plist_template=plist, entitlements=None,
        bundle_name="Probe.app", binary_name="probe", frameworks=("AppKit", "Carbon"),
        stamp_name=".probe", stamp_version=1, noun="app",
    )
    toolchain = _Toolchain()

    status, bundle = build_bundle(spec, bin_dir, runner=toolchain)

    assert status == "built"
    assert bundle == bin_dir / "Probe.app"
    swiftc, sign = toolchain.calls
    assert swiftc[0][:7] == ["xcrun", "swiftc", "-O", "-framework", "AppKit", "-framework", "Carbon"]
    assert "--entitlements" not in sign[0]
    stamp = json.loads((bin_dir / ".probe").read_text(encoding="utf-8"))
    assert list(stamp) == ["stamp_version", "source_sha256", "plist_sha256", "binary_sha256"]
    assert install_module.bundle_is_current(spec, bin_dir)
    # Two bundles share a bin dir without touching each other's stamp.
    build_bundle(RECORDER_SPEC, bin_dir, runner=_Toolchain())
    assert install_module.bundle_is_current(spec, bin_dir)
    assert install_module.recorder_is_current(bin_dir)


def test_a_missing_app_source_names_the_app_not_the_recorder(bin_dir, tmp_path):
    spec = BundleSpec(
        source=tmp_path / "gone.swift", plist_template=tmp_path / "gone.plist", entitlements=None,
        bundle_name="Probe.app", binary_name="probe", frameworks=(), stamp_name=".probe",
        stamp_version=1, noun="app",
    )
    with pytest.raises(install_module.InstallError, match="missing the app source"):
        build_bundle(spec, bin_dir, runner=_Toolchain())


# --- the app's own build (T-72/T-74) -----------------------------------


def test_the_app_source_and_template_ship_with_the_package():
    assert install_module.APP_SOURCE.is_file()
    assert install_module.APP_PLIST_TEMPLATE.is_file()
    assert install_module.APP_SOURCE.name == "VocalizeApp.swift"


def test_golden_app_spec_is_the_0_13_0_build():
    assert APP_SPEC == BundleSpec(
        source=install_module.APP_SOURCE,
        plist_template=install_module.APP_PLIST_TEMPLATE,
        entitlements=None,
        bundle_name="Vocalize.app",
        binary_name="vocalize-app",
        frameworks=("AppKit", "Carbon"),
        stamp_name=".app",
        stamp_version=1,
        noun="app",
    )


def test_the_app_build_assembles_a_signed_bundle(bin_dir):
    fake = _Toolchain()

    status, bundle = build_bundle(APP_SPEC, bin_dir, runner=fake)

    assert status == "built"
    assert bundle == bin_dir / "Vocalize.app"
    binary = bundle / "Contents" / "MacOS" / "vocalize-app"
    assert binary.is_file()
    plist = (bundle / "Contents" / "Info.plist").read_text(encoding="utf-8")
    assert plist == install_module.APP_PLIST_TEMPLATE.read_text(encoding="utf-8")
    swiftc, sign = fake.calls
    assert swiftc[0][:2] == ["xcrun", "swiftc"]
    assert "-framework" in swiftc[0] and "AppKit" in swiftc[0] and "Carbon" in swiftc[0]
    assert sign[0][:4] == ["codesign", "-s", "-", "--force"]
    assert "--entitlements" not in sign[0]
    stamp = json.loads((bin_dir / ".app").read_text(encoding="utf-8"))
    assert list(stamp) == ["stamp_version", "source_sha256", "plist_sha256", "binary_sha256"]
    assert stamp["stamp_version"] == 1
    assert install_module.bundle_is_current(APP_SPEC, bin_dir)


def test_an_unchanged_app_is_not_rebuilt(bin_dir):
    build_bundle(APP_SPEC, bin_dir, runner=_Toolchain())
    second = _Toolchain()

    status, _ = build_bundle(APP_SPEC, bin_dir, runner=second)

    assert status == "current"
    assert second.calls == []


def test_a_changed_app_source_is_rebuilt(bin_dir, tmp_path):
    build_bundle(APP_SPEC, bin_dir, runner=_Toolchain())
    changed_source = tmp_path / "VocalizeApp.swift"
    changed_source.write_text(
        install_module.APP_SOURCE.read_text(encoding="utf-8") + "\n// one more line\n",
        encoding="utf-8",
    )
    spec = replace(APP_SPEC, source=changed_source)
    fake = _Toolchain()

    status, _ = build_bundle(spec, bin_dir, runner=fake)

    assert status == "rebuilt"
    assert fake.programs == ["xcrun", "codesign"]


def test_an_app_bundle_with_a_missing_stamp_is_rebuilt(bin_dir):
    build_bundle(APP_SPEC, bin_dir, runner=_Toolchain())
    install_module.bundle_stamp_path(APP_SPEC, bin_dir).unlink()
    fake = _Toolchain()

    status, _ = build_bundle(APP_SPEC, bin_dir, runner=fake)

    # "rebuilt", not "built": the bundle was already there and already
    # granted Accessibility under its old signature.
    assert status == "rebuilt"
    assert fake.programs == ["xcrun", "codesign"]


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="no Swift toolchain")
def test_the_app_source_parses():
    result = subprocess.run(
        ["xcrun", "swiftc", "-parse", str(install_module.APP_SOURCE)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if "unable to find utility" in result.stderr or "license" in result.stderr.lower():
        pytest.skip("the Command Line Tools are not usable on this machine")
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("plutil") is None, reason="not macOS")
def test_the_app_plist_template_lints():
    result = subprocess.run(
        ["plutil", "-lint", str(install_module.APP_PLIST_TEMPLATE)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
