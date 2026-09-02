"""The recorder bundle: its Swift source, its plist, and the build at install.

Nothing here needs Xcode. The build is driven through a fake toolchain
(`FakeToolchain` below) that writes a stand-in binary where `swiftc -o`
points and records what `codesign` was handed, so the layout, the stamp,
the rebuild rule and the two "your toolchain is broken" diagnoses are all
covered on a machine with no compiler at all.

The two checks that do need the real tools — `swiftc -parse` on the Swift
source and `plutil -lint` on the substituted plist — skip themselves when
the tool is missing.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from vocalize.cli import _build_recorder_step
from vocalize.local import install as install_module


@pytest.fixture
def bin_dir(tmp_path):
    return tmp_path / "bin"


class FakeToolchain:
    """A stand-in for subprocess.run covering both build steps.

    `compile_fails` / `sign_fails` take the (returncode, stderr) a broken
    toolchain would produce; `missing` names programs that are not
    installed at all, which is a FileNotFoundError, not an exit code.
    """

    def __init__(self, *, compile_fails=None, sign_fails=None, missing=()):
        self.calls = []
        self.compile_fails = compile_fails
        self.sign_fails = sign_fails
        self.missing = set(missing)

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        program = Path(argv[0]).name
        if program in self.missing:
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        if program == "codesign":
            if self.sign_fails:
                code, stderr = self.sign_fails
                return subprocess.CompletedProcess(argv, code, "", stderr)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if self.compile_fails:
            code, stderr = self.compile_fails
            return subprocess.CompletedProcess(argv, code, "", stderr)
        target = Path(argv[argv.index("-o") + 1])
        target.write_bytes(b"fake-mach-o")
        target.chmod(0o755)
        return subprocess.CompletedProcess(argv, 0, "", "")

    @property
    def programs(self):
        return [Path(argv[0]).name for argv, _ in self.calls]


# --- the shipped source and template ----------------------------------


def test_the_recorder_source_and_template_ship_with_the_package():
    assert install_module.RECORDER_SOURCE.is_file()
    assert install_module.RECORDER_PLIST_TEMPLATE.is_file()
    assert install_module.RECORDER_SOURCE.parent.name == "recorder"


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="no Swift toolchain")
def test_the_swift_source_parses():
    result = subprocess.run(
        ["xcrun", "swiftc", "-parse", str(install_module.RECORDER_SOURCE)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if "unable to find utility" in result.stderr or "license" in result.stderr.lower():
        pytest.skip("the Command Line Tools are not usable on this machine")
    assert result.returncode == 0, result.stderr


def test_the_source_never_prints_what_it_records():
    """The recorder's stdout is a contract: device names and status words.

    Anything that wrote samples, a transcript or the output file's contents
    to stdout would put the user's voice in a log; the only `print(` calls
    in the file are the two the contract names.
    """
    printed = re.findall(
        r"print\(.*\)", install_module.RECORDER_SOURCE.read_text(encoding="utf-8")
    )
    assert sorted(printed) == sorted([
        "print(device.name)",
        "print(word)",
        'print("device: \\(device?.name ?? "none")")',
    ])


def test_check_mode_never_asks_for_permission():
    """--check reports; only the recording path may prompt.

    `AVCaptureDevice.requestAccess` is what puts the system dialog on
    screen. It must be reachable from exactly one place, and that place
    must sit after --check has already exited — otherwise the command a
    user runs to ask "do I have the microphone?" would itself demand it.
    """
    source = install_module.RECORDER_SOURCE.read_text(encoding="utf-8")

    assert source.count("AVCaptureDevice.requestAccess") == 1
    assert source.count("requestMicrophoneAccess()") == 2  # the definition and one call
    assert source.index("if options.check {") < source.index("let granted = requestMicrophoneAccess()")


def test_check_mode_exits_before_anything_opens_the_microphone():
    """The --check branch ends in finish(...) on every authorization state,
    so control never reaches the recording code below it."""
    source = install_module.RECORDER_SOURCE.read_text(encoding="utf-8")
    branch = source[source.index("if options.check {") : source.index("guard let outPath")]

    assert branch.count("finish(") == 1  # one exit, after the switch has picked the code
    assert "AVAudioRecorder(" not in branch
    assert "AVAudioEngine(" not in branch


def test_the_recorder_takes_its_pid_file_with_it_when_it_is_killed():
    """design.md promises rec.pid is removed on exit, and `dictate`'s
    max-seconds backstop is a SIGTERM. Without a handler the promise is false
    in exactly the case the contract was written for."""
    source = install_module.RECORDER_SOURCE.read_text(encoding="utf-8")

    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        assert f"signal({name}, onSignal)" in source
    handler = source[source.index("func onSignal("):source.index("func installSignalHandlers")]
    # Only async-signal-safe calls, and the PID file goes first.
    assert "unlink(path)" in handler
    assert handler.index("unlink(path)") < handler.index("_exit(")
    assert "installSignalHandlers()" in source.split("// MARK: - main")[1]


def test_an_empty_device_name_means_the_system_default():
    """`[stt] input_device = ""` is the documented way to say "the default",
    and it reaches --device as an empty string. Looking that up as a device
    name would fail on a machine with a perfectly good microphone."""
    source = install_module.RECORDER_SOURCE.read_text(encoding="utf-8")

    assert "guard let wanted, !wanted.isEmpty else { return defaultInputDevice() }" in source
    assert "let namedDevice = options.device?.isEmpty == false" in source


def test_the_tap_and_the_stop_are_serialised_on_the_output_file():
    """`removeTap` does not wait for an in-flight callback, so the audio
    thread's read of `file` and the stop's store of nil are a data race on a
    strong reference — which crashes the recorder mid-recording."""
    source = install_module.RECORDER_SOURCE.read_text(encoding="utf-8")
    body = source[source.index("final class DeviceRecorder"):source.index("// MARK: - Arguments")]

    assert "private let lock = NSLock()" in body
    assert body.count("lock.lock()") == 2  # the stop, and the tap callback
    # The engine stops feeding the tap before the tap is taken away.
    assert body.index("engine.stop()") < body.index("input.removeTap(onBus: 0)\n        lock")


def test_the_plist_template_carries_the_permission_identity():
    text = install_module.RECORDER_PLIST_TEMPLATE.read_text(encoding="utf-8")

    assert "cards.arda.vocalize.recorder" in text
    assert "Vocalize Recorder" in text
    assert "NSMicrophoneUsageDescription" in text
    assert "Audio never leaves this Mac." in text
    assert "LSUIElement" in text
    # LSBackgroundOnly declares an app that can never come to the foreground.
    # Nothing needs it (LSUIElement already keeps the recorder out of the Dock
    # and out of focus) and it is the one key that could plausibly suppress the
    # microphone prompt this bundle exists to be named in.
    assert "<key>LSBackgroundOnly</key>" not in text
    # Nothing is substituted any more: the plist IS the identity, so a version
    # in it would change the signature — and drop the grant — every release.
    assert "__VERSION__" not in text


@pytest.mark.skipif(shutil.which("plutil") is None, reason="not macOS")
def test_the_substituted_plist_lints(bin_dir):
    install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())
    plist = install_module.recorder_bundle(bin_dir) / "Contents" / "Info.plist"

    result = subprocess.run(
        ["plutil", "-lint", str(plist)], capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


# --- the build --------------------------------------------------------


def test_the_build_assembles_a_signed_bundle(bin_dir):
    fake = FakeToolchain()

    status, bundle = install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    assert status == "built"
    assert bundle == bin_dir / "Vocalize Recorder.app"
    binary = bundle / "Contents" / "MacOS" / "recorder"
    assert binary.is_file()
    plist = (bundle / "Contents" / "Info.plist").read_text(encoding="utf-8")
    assert plist == install_module.RECORDER_PLIST_TEMPLATE.read_text(encoding="utf-8")
    assert "cards.arda.vocalize.recorder" in plist
    # codesign runs last, over a bundle that already has both parts.
    assert fake.programs == ["xcrun", "codesign"]
    signed = Path(fake.calls[-1][0][-1])
    assert fake.calls[-1][0][:4] == ["codesign", "-s", "-", "--force"]
    # Signed in staging: the granted bundle is only ever replaced whole.
    assert signed.parent == bin_dir
    assert signed != bundle


def test_the_signature_turns_the_hardened_runtime_on(bin_dir):
    """The recorder is the only process on the machine holding a microphone
    grant. Without the hardened runtime it honours DYLD_INSERT_LIBRARIES, so
    anything already running as the user could record under its grant."""
    fake = FakeToolchain()

    install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    assert "--options" in fake.calls[-1][0]
    assert fake.calls[-1][0][fake.calls[-1][0].index("--options") + 1] == "runtime"


def test_the_compile_argv_is_a_list_naming_only_our_own_paths(bin_dir):
    fake = FakeToolchain()

    install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    argv, kwargs = fake.calls[0]
    assert argv[:2] == ["xcrun", "swiftc"]
    assert "-O" in argv
    assert argv[-1] == str(install_module.RECORDER_SOURCE)
    target = Path(argv[argv.index("-o") + 1])
    # Compiled into a staging bundle, never over the granted one.
    assert target.parts[-3:] == ("Contents", "MacOS", "recorder")
    assert target.parents[3] == bin_dir
    assert target != install_module.recorder_binary(bin_dir)
    assert "-framework" in argv and "AVFoundation" in argv and "CoreAudio" in argv
    # No shell anywhere: a bundle path with a space in it is an argv entry,
    # never a string a shell would split.
    assert kwargs.get("shell", False) is False
    assert kwargs["check"] is False
    assert kwargs["timeout"] > 0
    for _argv, _kwargs in fake.calls:
        assert _kwargs.get("shell", False) is False


def test_the_stamp_records_the_source_hash(bin_dir):
    install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())

    stamp = json.loads(install_module.recorder_stamp_path(bin_dir).read_text(encoding="utf-8"))

    import hashlib

    expected = hashlib.sha256(install_module.RECORDER_SOURCE.read_bytes()).hexdigest()
    assert stamp["source_sha256"] == expected
    # The binary is stamped too: everything else in install.py is verified
    # against its stamp before it is trusted, and this is the one we execute.
    binary = install_module.recorder_binary(bin_dir)
    assert stamp["binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    # The vocalize version is NOT in the stamp: it would rebuild a
    # byte-identical recorder on every release and drop the microphone grant.
    assert "version" not in stamp


def test_an_unchanged_source_is_not_rebuilt(bin_dir):
    install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())
    second = FakeToolchain()

    status, _ = install_module.build_recorder(bin_dir=bin_dir, runner=second)

    assert status == "current"
    assert second.calls == []


def test_a_changed_source_is_rebuilt_and_reported_as_such(bin_dir, tmp_path, monkeypatch):
    install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())
    changed = tmp_path / "VocalizeRecorder.swift"
    changed.write_text(
        install_module.RECORDER_SOURCE.read_text(encoding="utf-8") + "\n// one more line\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(install_module, "RECORDER_SOURCE", changed)
    fake = FakeToolchain()

    status, _ = install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    assert status == "rebuilt"
    assert fake.programs == ["xcrun", "codesign"]


def test_a_new_vocalize_version_does_not_cost_the_user_the_microphone(bin_dir, monkeypatch):
    """An upgrade that does not touch the recorder must not rebuild it.

    A rebuild is a new ad-hoc signature, which is a new TCC identity: if the
    vocalize version were part of the bundle, every release would silently
    revoke dictation until the user re-approved it in System Settings.
    """
    install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())
    monkeypatch.setattr("vocalize.__version__", "9.9.10", raising=False)
    second = FakeToolchain()

    status, _ = install_module.build_recorder(bin_dir=bin_dir, runner=second)

    assert status == "current"
    assert second.calls == []


def test_a_deleted_bundle_is_built_again_even_with_a_stamp(bin_dir):
    install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())
    shutil.rmtree(install_module.recorder_bundle(bin_dir))
    fake = FakeToolchain()

    status, _ = install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    # "rebuilt", not "built": whatever grant the old signature had is gone.
    assert status == "rebuilt"
    assert install_module.recorder_binary(bin_dir).is_file()


def test_a_corrupt_stamp_still_warns_that_the_grant_is_gone(bin_dir):
    """The bundle is intact and granted; only its stamp was truncated. The
    rebuild that follows replaces the signature, so the user has to be told —
    the status comes from the artifact on disk, not from a readable stamp."""
    install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())
    install_module.recorder_stamp_path(bin_dir).write_text("{not json", encoding="utf-8")

    status, _ = install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())

    assert status == "rebuilt"
    assert install_module.read_recorder_stamp(bin_dir) is not None


def test_a_swapped_binary_under_a_valid_stamp_is_rebuilt(bin_dir):
    """Anything that can write to the cache directory could drop its own
    binary into the bundle and leave the stamp alone. The stamp records the
    binary's hash, so the swap is caught and the real one is rebuilt."""
    install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())
    install_module.recorder_binary(bin_dir).write_bytes(b"#!/bin/sh\nexit 0\n")
    fake = FakeToolchain()

    status, _ = install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    assert status == "rebuilt"
    assert fake.programs == ["xcrun", "codesign"]
    assert install_module.recorder_binary(bin_dir).read_bytes() == b"fake-mach-o"


def test_a_missing_recorder_source_is_reported_not_traced(bin_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(install_module, "RECORDER_SOURCE", tmp_path / "gone.swift")

    with pytest.raises(install_module.InstallError) as excinfo:
        install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())

    assert "reinstall vocalize" in str(excinfo.value)


# --- toolchain diagnoses ----------------------------------------------


def test_a_missing_swiftc_names_xcode_select_install(bin_dir):
    fake = FakeToolchain(missing={"xcrun"})

    with pytest.raises(install_module.InstallError) as excinfo:
        install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    assert "xcode-select --install" in str(excinfo.value)
    assert not install_module.recorder_stamp_path(bin_dir).exists()


def test_a_swiftc_that_xcrun_cannot_find_names_xcode_select_install(bin_dir):
    fake = FakeToolchain(
        compile_fails=(72, 'xcrun: error: unable to find utility "swiftc", not a developer tool')
    )

    with pytest.raises(install_module.InstallError) as excinfo:
        install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    assert "xcode-select --install" in str(excinfo.value)


def test_an_unaccepted_license_names_the_license_command(bin_dir):
    fake = FakeToolchain(compile_fails=(69, (
        "You have not agreed to the Xcode license agreements. You must agree to "
        "both license agreements below in order to build software. Run "
        "'sudo xcodebuild -license' as root."
    )))

    with pytest.raises(install_module.InstallError) as excinfo:
        install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    message = str(excinfo.value)
    assert "sudo xcodebuild -license accept" in message
    assert "xcode-select --install" not in message


def test_an_ordinary_compiler_error_reports_its_last_line(bin_dir):
    fake = FakeToolchain(compile_fails=(1, "VocalizeRecorder.swift:12:1: error: nope"))

    with pytest.raises(install_module.InstallError) as excinfo:
        install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    assert "error: nope" in str(excinfo.value)
    assert "compiled" in str(excinfo.value)


def test_a_failed_signature_is_reported_and_leaves_no_stamp(bin_dir):
    fake = FakeToolchain(sign_fails=(1, "codesign: bundle format unrecognized"))

    with pytest.raises(install_module.InstallError) as excinfo:
        install_module.build_recorder(bin_dir=bin_dir, runner=fake)

    assert "signed" in str(excinfo.value)
    # No stamp, so the next install tries again rather than trusting an
    # unsigned bundle macOS will never grant the microphone.
    assert not install_module.recorder_stamp_path(bin_dir).exists()


def test_a_failed_signature_leaves_the_granted_bundle_untouched(bin_dir, tmp_path, monkeypatch):
    """A build is all-or-nothing.

    Half a build — a new binary and plist under the previous signature — is
    worse than no build: the bundle still reports "built", and macOS kills it
    on launch for a signature that no longer validates.
    """
    install_module.build_recorder(bin_dir=bin_dir, runner=FakeToolchain())
    binary = install_module.recorder_binary(bin_dir)
    binary.write_bytes(b"the-granted-mach-o")
    install_module.recorder_stamp_path(bin_dir).write_text(
        json.dumps({
            **install_module._recorder_fingerprint(),
            "binary_sha256": __import__("hashlib").sha256(b"the-granted-mach-o").hexdigest(),
        }), encoding="utf-8",
    )
    changed = tmp_path / "VocalizeRecorder.swift"
    changed.write_text(
        install_module.RECORDER_SOURCE.read_text(encoding="utf-8") + "\n// changed\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(install_module, "RECORDER_SOURCE", changed)

    with pytest.raises(install_module.InstallError):
        install_module.build_recorder(
            bin_dir=bin_dir, runner=FakeToolchain(sign_fails=(1, "codesign: failed")),
        )

    assert binary.read_bytes() == b"the-granted-mach-o"
    assert not list(bin_dir.glob(".recorder-build-*"))


# --- what the install command says ------------------------------------


def _probe(monkeypatch, outcome):
    """Run the CLI's build step with `build_recorder` replaced by `outcome`."""
    monkeypatch.setattr(install_module, "build_recorder", outcome)

    @click.command()
    def probe():
        _build_recorder_step(install_module)

    return CliRunner().invoke(probe, [])


def test_the_install_step_warns_to_re_grant_after_a_rebuild(monkeypatch):
    result = _probe(monkeypatch, lambda **kw: ("rebuilt", Path("/x/Vocalize Recorder.app")))

    assert result.exit_code == 0
    assert "re-grant the microphone" in result.output
    assert "Privacy & Security" in result.output


def test_the_install_step_does_not_warn_when_nothing_changed(monkeypatch):
    result = _probe(monkeypatch, lambda **kw: ("current", Path("/x/Vocalize Recorder.app")))

    assert result.exit_code == 0
    assert "re-grant" not in result.output
    assert "already built" in result.output


def test_the_install_step_mentions_the_first_prompt_on_a_first_build(monkeypatch):
    result = _probe(monkeypatch, lambda **kw: ("built", Path("/x/Vocalize Recorder.app")))

    assert result.exit_code == 0
    assert "microphone access" in result.output
    assert "re-grant" not in result.output


def test_the_install_step_passes_a_toolchain_failure_through_without_a_traceback(monkeypatch):
    def raise_it(**kwargs):
        raise install_module.InstallError(install_module._CLT_HINT)

    result = _probe(monkeypatch, raise_it)

    assert result.exit_code == 1
    assert "xcode-select --install" in result.output
    assert "Traceback" not in result.output
