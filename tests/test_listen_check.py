"""`vocalize listen --check` and `--list-devices` (T-32).

Both paths run a real subprocess, because both are boundaries a mocked-out
`subprocess.run` would prove nothing about:

* `--check` goes through LaunchServices (`open -W -n -a <bundle>`), because
  TCC answers for the *responsible* process and a binary exec'd by the
  shell is answered for by the shell. So the fake here is a fake `open`,
  and what it writes is the status file the recorder would have written.
* `--list-devices` needs no permission, so it still execs the binary — and
  the fake there is a three-line shell script where the real one lives.

`tests/conftest.py` points `install.BIN_DIR` at tmp_path, so neither fake
lands anywhere near the developer's own bundle.
"""

import stat

import pytest
from click.testing import CliRunner

from vocalize import cli, dictate
from vocalize.cli import main
from vocalize.local import install as install_module
from vocalize.local import whisper_manifest as stt_manifest

AUTHORIZED = "status: authorized\ndevice: Fake Microphone\nexit: 0\n"


def shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


@pytest.fixture
def fake_recorder(tmp_path):
    """Install a fake recorder binary. Returns a function to (re)write it."""

    def write(exit_code=0, stdout="Built-in Microphone\n", stderr=""):
        binary = install_module.recorder_binary()
        binary.parent.mkdir(parents=True, exist_ok=True)
        argv_log = tmp_path / "recorder-argv.txt"
        binary.write_text(
            "#!/bin/sh\n"
            f'printf %s "$*" > "{argv_log}"\n'
            f"printf '%s' {shell_quote(stdout)}\n"
            f"printf '%s' {shell_quote(stderr)} >&2\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        install_module.write_recorder_stamp()
        return argv_log

    return write


@pytest.fixture
def fake_open(tmp_path, monkeypatch, fake_recorder):
    """Stand in for /usr/bin/open, and write the status file it would carry.

    Returns a function taking the recorder's report (or None for a launch
    that never produced one) and giving back the path its argv is logged to,
    one argument per line.
    """

    def write(body=AUTHORIZED, exit_code=0, stderr=""):
        fake_recorder()  # the bundle has to look built before --check runs
        script = tmp_path / "fake-open"
        argv_log = tmp_path / "open-argv.txt"
        writer = (
            ""
            if body is None
            else f"printf '%s' {shell_quote(body)} > \"$1\"; "
        )
        script.write_text(
            "#!/bin/sh\n"
            f': > "{argv_log}"\n'
            f'for arg in "$@"; do printf \'%s\\n\' "$arg" >> "{argv_log}"; done\n'
            "while [ $# -gt 0 ]; do\n"
            f'  if [ "$1" = "--status-file" ]; then shift; {writer}fi\n'
            "  shift\n"
            "done\n"
            f"printf '%s' {shell_quote(stderr)} >&2\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        monkeypatch.setattr(cli, "_OPEN", str(script))
        return argv_log

    return write


@pytest.fixture
def installed_model(monkeypatch):
    """Make one STT model look installed.

    `installed()` is faked rather than fed a real model file: the real one
    is 148 MB, and nothing here is testing the verifier.
    """
    entry = stt_manifest.file_for("base.en")
    monkeypatch.setattr(
        install_module, "installed",
        lambda manifest, **kw: (kw.get("files") == [entry], ""),
    )
    return "base.en"


def run(*args):
    return CliRunner().invoke(main, ["listen", *args])


# --- the identity the check measures ----------------------------------


def test_the_check_asks_the_bundle_not_the_terminal(fake_open, fake_recorder):
    """The one thing this command exists to report.

    macOS attributes a microphone grant to the responsible process, so a
    recorder exec'd as a child of the shell reports the *terminal's* grant —
    which is not the identity dictation ever runs under. Launching the
    bundle through LaunchServices is what makes the answer be about
    "Vocalize Recorder".
    """
    recorder_argv = fake_recorder()
    argv_log = fake_open()

    run("--check")

    argv = argv_log.read_text(encoding="utf-8").splitlines()
    assert argv[:4] == ["-W", "-n", "-a", str(install_module.recorder_bundle())]
    assert argv[4:7] == ["--args", "--check", "--status-file"]
    # The recorder binary itself was never run: it logs its argv when it is.
    assert not recorder_argv.exists()


def test_the_check_passes_the_recorder_nothing_but_the_flag_and_its_status_file(fake_open):
    argv_log = fake_open()

    run("--check")

    argv = argv_log.read_text(encoding="utf-8").splitlines()
    assert len(argv) == 8  # -W -n -a <bundle> --args --check --status-file <path>
    # A 0700 directory of our own making, thrown away with the command.
    assert argv[7].endswith("/status")


def test_a_recorder_that_never_reports_back_says_so_and_does_not_claim_ready(fake_open):
    fake_open(body=None, exit_code=1, stderr="Unable to find application\n")

    result = run("--check")

    assert result.exit_code == 1
    assert "did not report back" in result.output
    assert "ready" not in result.output
    assert "Traceback" not in result.output


# --- the exit-code mapping --------------------------------------------


def test_authorized_reports_ready_and_names_the_device(fake_open, installed_model):
    fake_open(AUTHORIZED)

    result = run("--check")

    assert result.exit_code == 0
    assert "authorized" in result.output
    assert "Input device: Fake Microphone" in result.output
    assert "ready" in result.output


def test_denied_names_the_system_settings_pane(fake_open):
    fake_open("status: denied\ndevice: Fake Microphone\nexit: 2\n")

    result = run("--check")

    assert result.exit_code == 2
    assert "denied" in result.output
    assert "Privacy & Security" in result.output
    assert "Vocalize Recorder" in result.output


def test_a_missing_device_names_the_list_command(fake_open):
    fake_open("status: authorized\ndevice: none\nexit: 3\n")

    result = run("--check")

    assert result.exit_code == 3
    assert "Input device: none" in result.output
    assert "vocalize listen --list-devices" in result.output


def test_not_determined_says_the_prompt_has_not_happened_yet(fake_open):
    fake_open("status: notDetermined\ndevice: Fake Microphone\nexit: 5\n")

    result = run("--check")

    assert result.exit_code == 5
    assert "notDetermined" in result.output
    assert "has not asked yet" in result.output


def test_a_restricted_microphone_says_why_the_pane_will_not_help(fake_open):
    """A managed Mac reports "denied" — the three-word vocabulary has no
    word for "your employer turned this off" — so the reason has to travel
    alongside it, or the user is sent to a greyed-out pane."""
    fake_open(
        "status: denied\ndevice: Fake Microphone\nexit: 2\n"
        "note: microphone access is restricted by policy\n"
    )

    result = run("--check")

    assert result.exit_code == 2
    assert "restricted by policy" in result.output


def test_an_unknown_exit_code_gets_a_generic_message_and_no_traceback(fake_open):
    fake_open("status: unknown\ndevice: none\nexit: 9\n")

    result = run("--check")

    # Clamped: the documented codes are 0/1/2/3/5, so a recorder returning
    # anything else is reported as an incomplete install, not passed through.
    assert result.exit_code == 1
    assert "unexpected status 9" in result.output
    assert "vocalize local install --stt" in result.output
    assert "Traceback" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_an_unreadable_status_line_still_reports_a_word_for_the_code(fake_open):
    # A recorder that reported its code but no word must not turn into a
    # KeyError, and must not silently report "none" as an authorization.
    fake_open("device: Fake Microphone\nexit: 5\n")

    result = run("--check")

    assert result.exit_code == 5
    assert "notDetermined" in result.output


# --- what the check is allowed to say ---------------------------------


def test_the_check_reports_install_state(fake_open, tmp_path, monkeypatch):
    fake_open("status: notDetermined\ndevice: Fake Microphone\nexit: 5\n")
    monkeypatch.setattr(stt_manifest, "MODEL_DIR", tmp_path / "models")

    result = run("--check")

    assert "Model: none — run: vocalize local install --stt" in result.output
    assert str(install_module.recorder_bundle()) in result.output


def test_the_check_names_an_installed_model(fake_open, installed_model):
    fake_open()

    result = run("--check")

    assert "Model: base.en" in result.output


def test_authorized_with_no_model_is_not_ready(fake_open):
    """The line above says the model is missing; this one must not then say
    everything is fine — and neither must the exit status a Quick Action or
    a shell `&&` gates on."""
    fake_open(AUTHORIZED)

    result = run("--check")

    assert result.exit_code != 0
    assert "ready." not in result.output
    assert "install a model with: vocalize local install --stt" in result.output


def test_an_unbuilt_recorder_names_the_install_command():
    result = run("--check")

    assert result.exit_code == 1
    assert "not built" in result.output
    assert "vocalize local install --stt" in result.output
    assert "Traceback" not in result.output


def test_a_device_name_cannot_smuggle_escape_sequences_into_the_terminal(fake_open):
    # A USB device names itself; the name reaches the terminal through us.
    fake_open("status: authorized\ndevice: Evil\x1b[2JMic\nexit: 0\n")

    result = run("--check")

    # ESC is gone, so what is left is inert text, not a screen-clearing
    # escape sequence.
    assert "\x1b" not in result.output
    assert "Input device: Evil[2JMic" in result.output


def test_a_recorder_note_cannot_smuggle_escape_sequences_either(fake_open):
    fake_open(
        "status: denied\ndevice: Fake Microphone\nexit: 2\nnote: Evil\x1b[2Jnote\n"
    )

    result = run("--check")

    assert "\x1b" not in result.output


def test_a_very_long_device_name_is_cut_to_the_documented_shape(fake_open):
    fake_open("status: authorized\ndevice: " + "M" * 400 + "\nexit: 0\n")

    result = run("--check")

    assert "M" * 128 in result.output
    assert "M" * 129 not in result.output


# --- --list-devices ---------------------------------------------------


def test_list_devices_prints_one_name_per_line(fake_recorder):
    argv_log = fake_recorder(0, "Built-in Microphone\nUSB Audio\n")

    result = run("--list-devices")

    assert result.exit_code == 0
    assert result.output.splitlines() == ["Built-in Microphone", "USB Audio"]
    assert argv_log.read_text(encoding="utf-8") == "--list-devices"


def test_a_listed_name_is_byte_identical_to_what_the_recorder_enumerated(fake_recorder):
    """These names are documented as copy-paste values for
    `[stt] input_device`, and the recorder compares them exactly."""
    name = "Scarlett 2i2 USB (Focusrite) #2"
    fake_recorder(0, f"{name}\n")

    result = run("--list-devices")

    assert result.output.splitlines() == [name]


def test_a_name_that_cannot_be_printed_verbatim_is_marked_as_unusable(fake_recorder):
    """A non-breaking space is not printable, so it is dropped on the way to
    the terminal — and the cleaned name would never match the real one. Say
    so, rather than handing over a value that silently fails every
    dictation with "no input device named ..."."""
    fake_recorder(0, "Focusrite\xa0Scarlett\n")

    result = run("--list-devices")

    assert result.exit_code == 0
    assert "cannot be used as [stt] input_device" in result.output


def test_list_devices_says_so_when_there_are_none(fake_recorder):
    fake_recorder(0, "")

    result = run("--list-devices")

    assert result.exit_code == 0
    assert "No input devices found." in result.output


def test_list_devices_reports_a_failing_recorder_without_a_traceback(fake_recorder):
    fake_recorder(3, "", "no audio hardware\n")

    result = run("--list-devices")

    assert result.exit_code == 3
    assert "could not list input devices" in result.output
    assert "Traceback" not in result.output


def test_a_failure_message_cannot_smuggle_escape_sequences_into_the_terminal(fake_recorder):
    """The failure path echoes the recorder's own stderr, which can quote a
    hardware-supplied name — the same untrusted string the success path is
    already hardened against."""
    fake_recorder(3, "", "device Evil\x1b[2JMic went away\n")

    result = run("--list-devices")

    assert "\x1b" not in result.output


def test_an_unbuilt_recorder_is_reported_by_list_devices_too():
    result = run("--list-devices")

    assert result.exit_code == 1
    assert "not built" in result.output


# --- what the check leaves behind for `vocalize status` ---------------


def test_the_check_records_what_it_saw_for_the_status_screen(fake_open, installed_model):
    from vocalize import dictate

    fake_open(AUTHORIZED)

    run("--check")

    assert dictate.read_mic_status() == "authorized"


def test_a_recorder_that_never_reported_is_recorded_as_incomplete(fake_open):
    from vocalize import dictate

    fake_open(None)

    run("--check")

    assert dictate.read_mic_status() == "incomplete"


# --- recording, transcribing and the toggle (T-41) --------------------


def test_the_toggle_flag_runs_the_state_machine(monkeypatch):
    from vocalize import dictate

    seen = {}
    monkeypatch.setattr(dictate, "toggle", lambda stt: seen.setdefault("stt", stt) and 0)

    result = run("--toggle")

    assert result.exit_code == 0
    assert seen["stt"]["model"]  # the resolved [stt] table reached it


def test_the_dictate_command_is_the_toggle_under_another_name(monkeypatch):
    from vocalize import dictate

    calls = []
    monkeypatch.setattr(dictate, "toggle", lambda stt: calls.append(stt) or 0)

    result = CliRunner().invoke(main, ["dictate"])

    assert result.exit_code == 0
    assert len(calls) == 1


def test_the_dictate_command_passes_the_cleanup_flag_through(monkeypatch):
    from vocalize import dictate

    calls = []
    monkeypatch.setattr(dictate, "toggle", lambda stt: calls.append(stt) or 0)

    CliRunner().invoke(main, ["dictate", "--cleanup", "--max-seconds", "45"])

    assert calls[0]["cleanup"] is True
    assert calls[0]["max_seconds"] == 45


def test_the_toggles_exit_code_is_the_commands_exit_code(monkeypatch):
    from vocalize import dictate

    monkeypatch.setattr(dictate, "toggle", lambda stt: 1)

    assert run("--toggle").exit_code == 1


def test_the_cancel_flag_cancels(monkeypatch):
    from vocalize import dictate

    calls = []
    monkeypatch.setattr(dictate, "cancel", lambda stt: calls.append(stt) or 0)

    assert run("--cancel").exit_code == 0
    assert len(calls) == 1


def test_a_bare_listen_prints_the_transcript_on_stdout(monkeypatch):
    from vocalize import dictate

    monkeypatch.setattr(dictate, "listen", lambda stt, wait: "Hello there.")

    result = run()

    assert result.exit_code == 0
    assert result.output.strip() == "Hello there."


def test_a_silent_recording_exits_one_and_prints_no_transcript(monkeypatch):
    from vocalize import dictate

    monkeypatch.setattr(dictate, "listen", lambda stt, wait: None)

    result = run()

    assert result.exit_code == 1
    assert "Nothing heard" in result.output


def test_max_seconds_outside_the_allowed_range_is_refused():
    for value in ("0", "601", "-5"):
        assert run("--max-seconds", value).exit_code == 2


def test_two_modes_at_once_is_a_usage_error():
    result = run("--toggle", "--cancel")

    assert result.exit_code == 2
    assert "only one of" in result.output


def test_the_wav_flag_transcribes_the_named_file(monkeypatch, tmp_path):
    from vocalize import dictate

    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"")
    monkeypatch.setattr(dictate, "transcribe_wav", lambda path, stt: f"read {path.name}")

    result = run("--wav", str(clip))

    assert result.exit_code == 0
    assert result.output.strip() == "read clip.wav"


def test_a_malformed_wav_is_a_clean_error_not_a_traceback(tmp_path, monkeypatch):
    from vocalize import dictate
    from vocalize.local import install as install_mod

    monkeypatch.setattr(dictate, "_uv_or_raise", lambda: "/nonexistent/uv")
    monkeypatch.setattr(install_mod, "installed", lambda manifest, **kw: (True, ""))
    junk = tmp_path / "broken.wav"
    junk.write_bytes(b"RIFFnot really a wav")

    result = run("--wav", str(junk))

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "WAV" in result.output
    assert "Traceback" not in result.output


# --- what the 0.10.0 release review found (DEC-014) -------------------


def _write_config(text: str) -> None:
    path = cli.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_check_measures_the_configured_input_device(fake_open):
    """`input_device` exists because the default input was the wrong one.

    Measuring the system default and then saying "ready" is the failure
    this command is supposed to catch, not commit.
    """
    _write_config('[stt]\ninput_device = "MacBook Pro Microphone"\n')
    argv_log = fake_open()

    run("--check")

    argv = argv_log.read_text(encoding="utf-8").splitlines()
    assert argv[8:] == ["--device", "MacBook Pro Microphone"]


def test_an_empty_input_device_still_means_the_system_default(fake_open):
    _write_config('[stt]\ninput_device = ""\n')
    argv_log = fake_open()

    run("--check")

    assert "--device" not in argv_log.read_text(encoding="utf-8").splitlines()


def test_a_granted_microphone_with_no_device_is_not_recorded_as_unknown(
    fake_open, installed_model
):
    """Exit 3 is "no usable input device", not "we do not know the grant".

    The recorder reports `status: authorized` with that code, and the
    missing device already has its own `vocalize status` row — writing
    "unknown" over a grant we do know about reported the same problem
    twice, once wrongly.
    """
    fake_open(body="status: authorized\ndevice: none\nexit: 3\n")

    run("--check")

    assert dictate.read_mic_status() == "authorized"


def test_a_recorder_that_never_reports_records_an_incomplete_install(fake_open):
    fake_open(body=None, exit_code=1, stderr="Unable to find application\n")

    run("--check")

    assert dictate.read_mic_status() == "incomplete"
