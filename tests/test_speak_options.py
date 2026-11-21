import io
import subprocess

import pytest

from vocalize import speak_options

# --- test doubles -----------------------------------------------------------


class _Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _set_stdin(monkeypatch, text: str):
    monkeypatch.setattr(speak_options.sys, "stdin", io.TextIOWrapper(io.BytesIO(text.encode("utf-8"))))


class _Recorder:
    """Records subprocess.run calls and replies from a scripted queue by argv[0]/subcommand."""

    def __init__(self):
        self.calls = []
        self.settings = _Result(0, "overflow=ask\nmax_chars=1000\n")
        self.osascript_stdout = ""
        self.osascript_rc = 0
        self.osascript_exc = None
        self.speak_rc = 0

    def run(self, argv, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})
        prog = argv[0]
        if prog.endswith("vocalize") or prog == "vocalize":
            if len(argv) > 1 and argv[1] == "settings":
                return self.settings
            return _Result(self.speak_rc)  # speak-file
        if prog == "/usr/bin/osascript":
            if self.osascript_exc:
                raise self.osascript_exc
            return _Result(self.osascript_rc, self.osascript_stdout)
        raise AssertionError(f"unexpected subprocess: {argv}")

    def speak_calls(self):
        return [c for c in self.calls
                if (c["argv"][0].endswith("vocalize") or c["argv"][0] == "vocalize")
                and len(c["argv"]) > 1 and c["argv"][1] == "speak-file"]


@pytest.fixture
def rec(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(speak_options.subprocess, "run", r.run)
    monkeypatch.setenv("VOCALIZE_BIN", "/opt/vocalize")
    return r


def _run(monkeypatch, rec, text):
    _set_stdin(monkeypatch, text)
    return speak_options.main()


# --- stdin / bin resolution -------------------------------------------------


def test_reads_stdin_as_utf8_bytes(monkeypatch, rec):
    # A curly quote survives the bytes->utf8 decode intact.
    rec.settings = _Result(0, "overflow=truncate\nmax_chars=1000\n")  # fast path
    _run(monkeypatch, rec, "smart “quotes” here")
    spoken = rec.speak_calls()[0]["kwargs"]["input"]
    assert "“quotes”" in spoken


def test_missing_vocalize_bin_errors(monkeypatch, rec, capsys):
    monkeypatch.delenv("VOCALIZE_BIN", raising=False)
    monkeypatch.setattr(speak_options.shutil, "which", lambda name: None)
    assert _run(monkeypatch, rec, "hi") == 1
    assert "vocalize binary not found" in capsys.readouterr().err


def test_vocalize_bin_env_wins_over_which(monkeypatch, rec):
    monkeypatch.setattr(speak_options.shutil, "which",
                        lambda n: (_ for _ in ()).throw(AssertionError("which used")))
    rec.settings = _Result(0, "overflow=truncate\nmax_chars=1000\n")
    _run(monkeypatch, rec, "hi")
    assert rec.speak_calls()[0]["argv"][0] == "/opt/vocalize"


# --- settings parsing -------------------------------------------------------


def test_settings_parse_success_reaches_picker(monkeypatch, rec):
    rec.osascript_stdout = "Speak all"
    _run(monkeypatch, rec, "x" * 2000)
    assert any(c["argv"][0] == "/usr/bin/osascript" for c in rec.calls)


def test_settings_unset_max_chars_is_fast_path(monkeypatch, rec):
    rec.settings = _Result(0, "overflow=ask\nmax_chars=unset\n")
    _run(monkeypatch, rec, "x" * 5000)
    # cap None -> no picker
    assert not any(c["argv"][0] == "/usr/bin/osascript" for c in rec.calls)


def test_settings_nonzero_exit_falls_back(monkeypatch, rec):
    rec.settings = _Result(1, "")
    _run(monkeypatch, rec, "x" * 5000)
    assert not any(c["argv"][0] == "/usr/bin/osascript" for c in rec.calls)
    assert rec.speak_calls()  # still spoke via fallback


def test_settings_tolerates_the_stt_lines(rec):
    # `vocalize settings` gained stt.* lines in 0.10.0 (DEC-006). Same
    # invariant as the chain line: the picker's parser must ignore every
    # line it does not recognize rather than failing closed on new keys.
    rec.settings = _Result(
        0,
        "overflow=ask\nmax_chars=1000\nchain=kokoro,say\n"
        "stt.model=small.en\nstt.language=en\nstt.cleanup=false\nstt.max_seconds=120\n",
    )

    assert speak_options._read_settings("/opt/vocalize") == ("ask", 1000)


def test_settings_tolerates_a_chain_line(rec):
    # The chain=... line settings gained is additive; the parser only ever
    # looks for overflow=/max_chars= prefixes, so an unrecognized line must
    # not change the parsed (mode, cap).
    rec.settings = _Result(0, "overflow=ask\nmax_chars=1000\nchain=kokoro,say\n")

    assert speak_options._read_settings("/opt/vocalize") == ("ask", 1000)


def test_settings_missing_overflow_line_falls_back(monkeypatch, rec):
    rec.settings = _Result(0, "max_chars=1000\n")
    _run(monkeypatch, rec, "x" * 5000)
    assert not any(c["argv"][0] == "/usr/bin/osascript" for c in rec.calls)


def test_settings_bad_overflow_value_falls_back(monkeypatch, rec):
    rec.settings = _Result(0, "overflow=weird\nmax_chars=1000\n")
    _run(monkeypatch, rec, "x" * 5000)
    assert not any(c["argv"][0] == "/usr/bin/osascript" for c in rec.calls)


def test_settings_unparseable_max_chars_falls_back(monkeypatch, rec):
    rec.settings = _Result(0, "overflow=ask\nmax_chars=lots\n")
    _run(monkeypatch, rec, "x" * 5000)
    assert not any(c["argv"][0] == "/usr/bin/osascript" for c in rec.calls)


def test_settings_timeout_falls_back(monkeypatch, rec):
    def boom(argv, **kw):
        if argv[1:2] == ["settings"]:
            raise subprocess.TimeoutExpired(argv, 10)
        return _Result(0)
    monkeypatch.setattr(speak_options.subprocess, "run", boom)
    _set_stdin(monkeypatch, "x" * 5000)
    assert speak_options.main() == 0


# --- fast paths -------------------------------------------------------------


def test_empty_stdin_delegates_to_vocalize(monkeypatch, rec):
    _run(monkeypatch, rec, "   \n ")
    calls = rec.speak_calls()
    assert len(calls) == 1
    # settings was never consulted for empty input
    assert not any(c["argv"][1:2] == ["settings"] for c in rec.calls)


def test_mode_truncate_is_fast_path_even_over_cap(monkeypatch, rec):
    rec.settings = _Result(0, "overflow=truncate\nmax_chars=100\n")
    _run(monkeypatch, rec, "x" * 5000)
    assert not any(c["argv"][0] == "/usr/bin/osascript" for c in rec.calls)


def test_under_cap_is_fast_path(monkeypatch, rec):
    _run(monkeypatch, rec, "short")
    assert not any(c["argv"][0] == "/usr/bin/osascript" for c in rec.calls)


def test_fast_path_always_passes_ask_dialog_and_no_overflow_flags(monkeypatch, rec):
    _run(monkeypatch, rec, "short")
    argv = rec.speak_calls()[0]["argv"]
    assert "--ask-dialog" in argv
    assert "--overflow" not in argv
    assert "--max-chars" not in argv


# --- picker -----------------------------------------------------------------


def _picker_script(rec):
    return next(c["argv"][2] for c in rec.calls if c["argv"][0] == "/usr/bin/osascript"
                and "choose from list" in c["argv"][2])


def test_picker_options_are_speak_all_then_truncate_only():
    assert speak_options._build_picker_options(1000) == [
        ("all", "Speak all"),
        ("truncate", "Truncate to 1000 characters"),
    ]


def test_picker_list_offers_only_speak_all_and_truncate(monkeypatch, rec):
    rec.osascript_stdout = "false"
    _run(monkeypatch, rec, "x" * 2000)
    script = _picker_script(rec)
    assert 'choose from list {"Speak all", "Truncate to 1000 characters"} ' in script
    assert "summary" not in script.lower()


def test_picker_choice_runs_no_other_vocalize_command(monkeypatch, rec):
    rec.osascript_stdout = "Truncate to 1000 characters"
    _run(monkeypatch, rec, "x" * 2000)
    subcommands = [c["argv"][1] for c in rec.calls if c["argv"][0] == "/opt/vocalize"]
    assert subcommands == ["settings", "speak-file"]


def test_truncate_label_has_no_comma(monkeypatch, rec):
    options = speak_options._build_picker_options(1000)
    truncate_label = {k: v for k, v in options}["truncate"]
    assert truncate_label == "Truncate to 1000 characters"
    assert "," not in truncate_label


def test_picker_prompt_uses_thousands_separators(monkeypatch, rec):
    rec.osascript_stdout = "Speak all"
    _run(monkeypatch, rec, "x" * 4213)
    script = next(c["argv"][2] for c in rec.calls if c["argv"][0] == "/usr/bin/osascript"
                  and "choose from list" in c["argv"][2])
    assert "4,213 characters" in script
    assert "the cap is 1,000" in script


def test_picker_default_item_is_truncate(monkeypatch, rec):
    rec.osascript_stdout = "Speak all"
    _run(monkeypatch, rec, "x" * 2000)
    script = next(c["argv"][2] for c in rec.calls if c["argv"][0] == "/usr/bin/osascript"
                  and "choose from list" in c["argv"][2])
    assert 'default items {"Truncate to 1000 characters"}' in script


def test_picker_cancel_speaks_nothing(monkeypatch, rec):
    rec.osascript_stdout = "false"
    assert _run(monkeypatch, rec, "x" * 2000) == 0
    assert rec.speak_calls() == []


def test_picker_timeout_speaks_nothing(monkeypatch, rec):
    rec.osascript_exc = subprocess.TimeoutExpired(["osascript"], 40)
    assert _run(monkeypatch, rec, "x" * 2000) == 0
    assert rec.speak_calls() == []


def test_picker_unrecognized_output_speaks_nothing(monkeypatch, rec):
    rec.osascript_stdout = "Something Else Entirely"
    assert _run(monkeypatch, rec, "x" * 2000) == 0
    assert rec.speak_calls() == []


def test_picker_script_never_contains_input_text(monkeypatch, rec):
    secret = "CONFIDENTIALPHRASE" * 120  # long enough to trip the cap
    rec.osascript_stdout = "false"
    _run(monkeypatch, rec, secret)
    for c in rec.calls:
        if c["argv"][0] == "/usr/bin/osascript":
            assert "CONFIDENTIALPHRASE" not in c["argv"][2]


# --- choice dispatch --------------------------------------------------------


def test_choice_speak_all_uses_overflow_never(monkeypatch, rec):
    rec.osascript_stdout = "Speak all"
    _run(monkeypatch, rec, "x" * 2000)
    argv = rec.speak_calls()[0]["argv"]
    assert "--overflow" in argv and argv[argv.index("--overflow") + 1] == "never"
    assert "--max-chars" not in argv


def test_choice_truncate_uses_cap_and_truncate(monkeypatch, rec):
    rec.osascript_stdout = "Truncate to 1000 characters"
    _run(monkeypatch, rec, "x" * 2000)
    argv = rec.speak_calls()[0]["argv"]
    assert argv[argv.index("--overflow") + 1] == "truncate"
    assert argv[argv.index("--max-chars") + 1] == "1000"


def test_speak_call_interrupt_propagates_after_child_is_stopped(monkeypatch, rec):
    # subprocess.run stops its child on KeyboardInterrupt before re-raising;
    # the helper must not swallow the cancellation or fall back to speaking.
    rec.osascript_stdout = "Speak all"
    original = rec.run

    def interrupted(argv, **kwargs):
        if argv[1:2] == ["speak-file"]:
            raise KeyboardInterrupt
        return original(argv, **kwargs)

    monkeypatch.setattr(speak_options.subprocess, "run", interrupted)
    _set_stdin(monkeypatch, "x" * 2000)
    with pytest.raises(KeyboardInterrupt):
        speak_options.main()
