import builtins
import io
import subprocess
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

import vocalize.cli as cli_module
from vocalize.cli import main
from vocalize.config import resolve_provider_settings


def _patch_tts(monkeypatch, audio=b"fake-mp3-bytes", calls=None, echo_lines=()):
    """Replace the provider chain with a fake that records what it was asked.

    The seam moved from tts.synthesize to chain.run when the chain landed;
    the captured text and settings are the same two things the assertions
    below have always looked at. Pass `calls` to also collect the keyword
    arguments each chain_run call received.
    """
    captured_text = []
    captured_settings = []

    def fake_chain_run(text, **kwargs):
        captured_text.append(text)
        if calls is not None:
            calls.append(kwargs)
        overrides = dict(kwargs.get("overrides") or {})
        overrides.pop("api_key", None)
        captured_settings.append(
            resolve_provider_settings(
                kwargs["chain"][0], kwargs["file_config"], primary=True, **overrides
            )
        )
        for line in echo_lines:
            kwargs["echo"](line)
        return audio, kwargs["chain"][0], "mp3"

    monkeypatch.setattr(cli_module, "chain_run", fake_chain_run)
    played = {}
    monkeypatch.setattr(cli_module, "play_audio", lambda path: played.setdefault("path", path))
    return played, captured_text, captured_settings


def test_speak_writes_audio_file(monkeypatch, tmp_path):
    _patch_tts(monkeypatch)
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["speak", "hello world", "--api-key", "fake-key", "--output", str(out_file), "--no-play"],
    )

    assert result.exit_code == 0, result.output
    assert out_file.read_bytes() == b"fake-mp3-bytes"


def test_speak_plays_by_default(monkeypatch, tmp_path):
    played, _captured_text, _captured_settings = _patch_tts(monkeypatch)
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main, ["speak", "hi", "--api-key", "fake-key", "--output", str(out_file)]
    )

    assert result.exit_code == 0, result.output
    assert played["path"] == out_file


def test_speak_file_reads_from_stdin(monkeypatch, tmp_path):
    _patch_tts(monkeypatch)
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["speak-file", "-", "--api-key", "fake-key", "--output", str(out_file), "--no-play"],
        input="# Title\n\nSome body text.",
    )

    assert result.exit_code == 0, result.output
    assert out_file.exists()


def test_speak_file_reads_from_path(monkeypatch, tmp_path):
    _played, captured_text, _captured_settings = _patch_tts(monkeypatch)
    src = tmp_path / "notes.md"
    src.write_text("| a | b |\n|---|---|\n| 1 | 2 |\n")
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["speak-file", str(src), "--api-key", "fake-key", "--output", str(out_file), "--no-play"],
    )

    assert result.exit_code == 0, result.output
    assert out_file.exists()
    assert "Table with 1 row." in captured_text[0]
    assert "|" not in captured_text[0]


def test_raw_flag_skips_flattening(monkeypatch, tmp_path):
    _played, captured_text, _captured_settings = _patch_tts(monkeypatch)
    src = tmp_path / "notes.md"
    src.write_text("| a | b |\n|---|---|\n| 1 | 2 |\n")
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "speak-file", str(src), "--api-key", "fake-key",
            "--output", str(out_file), "--no-play", "--raw",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "| a | b |" in captured_text[0]
    assert "|---|---|" in captured_text[0]


def test_max_chars_truncates_and_notes(monkeypatch, tmp_path):
    _played, captured_text, _captured_settings = _patch_tts(monkeypatch)
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "speak", "hello world, this is a long sentence to truncate",
            "--api-key", "fake-key", "--output", str(out_file), "--no-play",
            "--max-chars", "10",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(captured_text[0]) <= 10
    assert "truncated" in result.output


def test_short_input_makes_one_chain_call_and_relays_its_progress(monkeypatch, tmp_path):
    # The per-chunk wording now comes from chain.run (tested in
    # test_chain.py); what the CLI owes is one call with the whole text and
    # every chain message relayed to stderr.
    _played, captured_text, _captured_settings = _patch_tts(
        monkeypatch, echo_lines=["Requesting 11 characters from elevenlabs..."]
    )
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["speak", "hello world", "--api-key", "fake-key", "--output", str(out_file), "--no-play"],
    )

    assert result.exit_code == 0, result.output
    assert captured_text == ["hello world"]
    assert "Requesting 11 characters from elevenlabs..." in result.output
    assert "Long input" not in result.output


def test_chunk_chars_flag_reaches_the_chain(monkeypatch, tmp_path):
    # Splitting itself is the chain's job now (test_chain.py); the CLI's
    # part is handing the flag over unchanged, None included.
    calls = []
    _patch_tts(monkeypatch, calls=calls)
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "speak", "some words", "--api-key", "fake-key",
            "--output", str(out_file), "--no-play", "--chunk-chars", "50",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["chunk_chars"] == 50


def test_missing_api_key_gives_clean_error(monkeypatch, tmp_path):
    # Forced to ElevenLabs, a missing key has nowhere to fall back to: the
    # chain's all-failed error still has to name the fix.
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    runner = CliRunner()

    result = runner.invoke(
        main, ["speak", "hello", "--no-play", "--provider", "elevenlabs"]
    )

    assert result.exit_code != 0
    from vocalize.exceptions import TTSRequestError

    assert isinstance(result.exception, TTSRequestError)
    assert "No ElevenLabs API key found" in str(result.exception)


def test_voices_command_lists_ids_and_names(monkeypatch):
    monkeypatch.setattr(cli_module, "build_client", lambda key: object())
    monkeypatch.setattr(
        cli_module,
        "list_voices",
        lambda client: [{"id": "abc", "name": "Rachel"}, {"id": "def", "name": "Josh"}],
    )
    runner = CliRunner()

    result = runner.invoke(main, ["voices", "--api-key", "fake-key"])

    assert result.exit_code == 0, result.output
    assert "abc\tRachel" in result.output
    assert "def\tJosh" in result.output


def test_speak_file_missing_path_gives_clean_error(tmp_path):
    missing = tmp_path / "nope.md"
    runner = CliRunner()

    result = runner.invoke(main, ["speak-file", str(missing)])

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "No such file or directory" in result.output


def test_speak_file_non_utf8_gives_clean_error(tmp_path):
    bad_file = tmp_path / "bad.md"
    bad_file.write_bytes(b"\xff\xfe\x9c")
    runner = CliRunner()

    result = runner.invoke(main, ["speak-file", str(bad_file)])

    assert result.exit_code != 0
    assert not isinstance(result.exception, UnicodeDecodeError)
    assert "UTF-8" in result.output
    assert str(bad_file) in result.output


def test_empty_text_reports_nothing_to_speak_without_a_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    runner = CliRunner()

    result = runner.invoke(main, ["speak", "", "--no-play"])

    assert result.exit_code != 0
    from vocalize.exceptions import TTSRequestError

    assert isinstance(result.exception, TTSRequestError)
    message = str(result.exception).lower()
    assert "empty" in message
    assert "api key" not in message


def test_run_reports_vocalize_error_and_exits_one(monkeypatch, capsys):
    from vocalize.exceptions import TTSRequestError

    def raise_error():
        raise TTSRequestError("boom")

    monkeypatch.setattr(cli_module, "main", raise_error)

    with pytest.raises(SystemExit) as excinfo:
        cli_module.run()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error: boom" in captured.err


def test_default_output_lands_in_cache_dir(monkeypatch, tmp_path):
    _patch_tts(monkeypatch)
    monkeypatch.setattr(cli_module, "DEFAULT_CACHE_DIR", tmp_path)
    runner = CliRunner()

    result = runner.invoke(main, ["speak", "hello", "--api-key", "fake-key", "--no-play"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "last.mp3").exists()


def _isolate_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-config"))
    for var in ("VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED"):
        monkeypatch.delenv(var, raising=False)


def test_speed_flag_reaches_the_settings(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _played, _captured_text, captured_settings = _patch_tts(monkeypatch)
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "speak", "hello", "--api-key", "fake-key",
            "--output", str(out_file), "--no-play", "--speed", "1.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_settings[0].speed == 1.1


def test_no_speed_flag_leaves_speed_unset(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _played, _captured_text, captured_settings = _patch_tts(monkeypatch)
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["speak", "hello", "--api-key", "fake-key", "--output", str(out_file), "--no-play"],
    )

    assert result.exit_code == 0, result.output
    assert captured_settings[0].speed is None


def test_usage_command_prints_tier_used_limit_and_percent(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "build_client", lambda key: object())
    monkeypatch.setattr(
        cli_module,
        "get_usage",
        lambda client: {"tier": "creator", "used": 12345, "limit": 100000, "resets_at": None},
    )
    # Empty tmp_path also covers the "cache empty" branch.
    monkeypatch.setattr(cli_module, "DEFAULT_CACHE_DIR", tmp_path)
    runner = CliRunner()

    result = runner.invoke(main, ["usage", "--api-key", "fake-key"])

    assert result.exit_code == 0, result.output
    assert "creator" in result.output
    assert "12,345" in result.output
    assert "100,000" in result.output
    assert "12.3%" in result.output
    assert "cache empty" in result.output


def test_usage_command_reports_local_cache_file_count(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    reset_unix = 1735689600
    expected_date = datetime.fromtimestamp(reset_unix, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")
    monkeypatch.setattr(cli_module, "build_client", lambda key: object())
    monkeypatch.setattr(
        cli_module,
        "get_usage",
        lambda client: {"tier": "free", "used": 0, "limit": 10000, "resets_at": reset_unix},
    )
    (tmp_path / "a.mp3").write_bytes(b"x" * 1000)
    (tmp_path / "b.mp3").write_bytes(b"y" * 2000)
    (tmp_path / "not-audio.txt").write_bytes(b"ignore me")
    monkeypatch.setattr(cli_module, "DEFAULT_CACHE_DIR", tmp_path)
    runner = CliRunner()

    result = runner.invoke(main, ["usage", "--api-key", "fake-key"])

    assert result.exit_code == 0, result.output
    assert "2 files" in result.output
    assert expected_date in result.output


def test_invalid_speed_gives_a_clean_error_not_a_traceback(monkeypatch, tmp_path, capsys):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["vocalize", "speak", "hello", "--api-key", "fake-key", "--no-play", "--speed", "5"],
    )

    with pytest.raises(SystemExit) as excinfo:
        cli_module.run()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("Error: ")
    assert "--speed" in captured.err
    assert "Traceback" not in captured.err


# --- overflow behaviour ------------------------------------------------------


def _isolate_overflow_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for var in ("VOCALIZE_OVERFLOW", "VOCALIZE_MAX_CHARS"):
        monkeypatch.delenv(var, raising=False)


def _speak_long(monkeypatch, tmp_path, extra_args, text="word " * 100):
    _played, captured_text, _settings = _patch_tts(monkeypatch)
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["speak", text, "--api-key", "fake-key", "--output", str(out_file), "--no-play",
         *extra_args],
    )
    return result, captured_text


def test_overflow_never_ignores_the_cap(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    result, captured = _speak_long(
        monkeypatch, tmp_path, ["--max-chars", "50", "--overflow", "never"]
    )

    assert result.exit_code == 0, result.output
    assert len(captured) == 1
    assert len(captured[0]) > 50
    assert "truncated" not in result.output


def test_overflow_truncate_is_the_default(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    result, captured = _speak_long(monkeypatch, tmp_path, ["--max-chars", "50"])

    assert result.exit_code == 0, result.output
    assert len(captured[0]) <= 50
    assert "truncated to 50" in result.output


def test_overflow_ask_without_a_terminal_degrades_to_truncate(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_module, "_ask_to_truncate", lambda n, cap: None)

    result, captured = _speak_long(
        monkeypatch, tmp_path, ["--max-chars", "50", "--overflow", "ask"]
    )

    assert result.exit_code == 0, result.output
    assert len(captured[0]) <= 50
    assert "no terminal to ask on" in result.output


def test_overflow_ask_speaks_everything_on_a_no(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    asked = {}
    monkeypatch.setattr(
        cli_module, "_ask_to_truncate",
        lambda n, cap: asked.update(chars=n, cap=cap) or False,
    )

    result, captured = _speak_long(
        monkeypatch, tmp_path, ["--max-chars", "50", "--overflow", "ask"]
    )

    assert result.exit_code == 0, result.output
    assert len(captured[0]) > 50
    assert asked["cap"] == 50
    assert asked["chars"] == len(captured[0])


def test_overflow_ask_truncates_on_a_yes(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_module, "_ask_to_truncate", lambda n, cap: True)

    result, captured = _speak_long(
        monkeypatch, tmp_path, ["--max-chars", "50", "--overflow", "ask"]
    )

    assert result.exit_code == 0, result.output
    assert len(captured[0]) <= 50


def test_overflow_ask_under_the_cap_never_prompts(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)

    def boom(n, cap):
        raise AssertionError("prompted even though input fits the cap")

    monkeypatch.setattr(cli_module, "_ask_to_truncate", boom)
    result, _captured = _speak_long(
        monkeypatch, tmp_path, ["--max-chars", "5000", "--overflow", "ask"]
    )

    assert result.exit_code == 0, result.output


def test_default_max_chars_caps_when_nothing_else_is_set(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    result, captured = _speak_long(monkeypatch, tmp_path, ["--default-max-chars", "50"])

    assert result.exit_code == 0, result.output
    assert len(captured[0]) <= 50
    assert "truncated to 50" in result.output


def test_env_var_beats_default_max_chars(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_MAX_CHARS", "80")
    result, captured = _speak_long(monkeypatch, tmp_path, ["--default-max-chars", "50"])

    assert result.exit_code == 0, result.output
    assert len(captured[0]) <= 80
    assert "truncated to 80" in result.output


def test_env_overflow_never_reaches_the_cli(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_OVERFLOW", "never")
    result, captured = _speak_long(monkeypatch, tmp_path, ["--max-chars", "50"])

    assert result.exit_code == 0, result.output
    assert len(captured[0]) > 50


def test_invalid_overflow_flag_is_rejected_at_the_flag_layer(monkeypatch, tmp_path):
    # The flag is a case-sensitive click Choice, unlike the case-insensitive
    # env/config coercion — "Never" must fail loudly, not silently truncate.
    _isolate_overflow_env(monkeypatch, tmp_path)
    result, captured = _speak_long(monkeypatch, tmp_path, ["--overflow", "Never"])

    assert result.exit_code == 2
    assert "--overflow" in result.output
    assert captured == []


def test_config_file_overflow_and_max_chars_reach_the_cli(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    cfg = tmp_path / "vocalize" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('max_chars = 60\noverflow = "truncate"\n', encoding="utf-8")

    result, captured = _speak_long(monkeypatch, tmp_path, [])

    assert result.exit_code == 0, result.output
    assert len(captured[0]) <= 60
    assert "truncated to 60" in result.output


def test_unknown_config_key_warns_once_per_run(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    cfg = tmp_path / "vocalize" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('bogus = "x"\n', encoding="utf-8")

    result, _captured = _speak_long(monkeypatch, tmp_path, [])

    assert result.exit_code == 0, result.output
    # Both resolvers share one parse; a typo'd key must not warn twice.
    assert result.output.count("unknown config key") == 1


def _fake_tty(monkeypatch, reply):
    """Route /dev/tty opens to in-memory streams; everything else is real."""
    real_open = builtins.open

    class _KeepValue(io.StringIO):
        final_value = ""

        def close(self):
            self.final_value = self.getvalue()
            super().close()

    written = _KeepValue()

    def fake_open(path, mode="r", *args, **kwargs):
        if path == "/dev/tty":
            return io.StringIO(reply) if "r" in mode else written
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    return written


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("n\n", False),
        ("no\n", False),
        ("No\n", False),
        ("  N  \n", False),
        ("y\n", True),
        ("\n", True),  # bare Enter takes the [Y/n] default
        ("nope\n", True),  # only n/no decline; anything else truncates
    ],
)
def test_ask_to_truncate_parses_real_tty_answers(monkeypatch, reply, expected):
    written = _fake_tty(monkeypatch, reply)
    assert cli_module._ask_to_truncate(5000, 100) is expected
    assert "5,000" in written.final_value
    assert "[Y/n]" in written.final_value


def test_ask_to_truncate_returns_none_on_tty_eof(monkeypatch):
    _fake_tty(monkeypatch, "")
    assert cli_module._ask_to_truncate(5000, 100) is None


def test_settings_prints_resolved_config_values(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    for var in ("VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED"):
        monkeypatch.delenv(var, raising=False)
    cfg = tmp_path / "vocalize" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('max_chars = 1000\noverflow = "ask"\n', encoding="utf-8")

    result = CliRunner().invoke(main, ["settings"])

    assert result.exit_code == 0, result.output
    assert "max_chars=1000" in result.output
    assert "overflow=ask" in result.output


def test_settings_prints_defaults_when_nothing_is_configured(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    for var in ("VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED"):
        monkeypatch.delenv(var, raising=False)

    result = CliRunner().invoke(main, ["settings"])

    assert result.exit_code == 0, result.output
    assert "max_chars=unset" in result.output
    assert "overflow=truncate" in result.output
    assert "speed=unset" in result.output


def test_stop_command_reports_a_stopped_player(monkeypatch):
    monkeypatch.setattr(cli_module, "stop_playback", lambda: True)
    result = CliRunner().invoke(main, ["stop"])
    assert result.exit_code == 0
    assert "Stopped playback." in result.output


def test_stop_command_reports_nothing_playing(monkeypatch):
    monkeypatch.setattr(cli_module, "stop_playback", lambda: False)
    result = CliRunner().invoke(main, ["stop"])
    assert result.exit_code == 0
    assert "Nothing is playing." in result.output


def test_ask_to_truncate_returns_none_without_a_tty(monkeypatch):
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/dev/tty":
            raise OSError("no controlling terminal")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    assert cli_module._ask_to_truncate(5000, 100) is None


# --- the credential guard behind `vocalize clip` ---------------------------


@pytest.mark.parametrize(
    "token",
    [
        "sk-abcdefetc",
        "pypi-AgEIcHlwaS5vcmc",
        "ghp_16charsofpadding",
        "github_pat_11ABCDE",
        "op://Employee/Example/some-item",  # the op:// prefix
        "eyJhbGciOiJIUzI1NiJ9",
        "xoxb-1234-abcd",
        "AKIAIOSFODNN7EXAMPLE",
        "glpat-xxxxxxxxxxxxxxxxxxxx",
        # No known prefix, but shaped like a generated key: long, one token,
        # letters+digits, high entropy.
        "aB3dE9fG1hJ4kL7mN0pQ2rS5",
    ],
)
def test_credential_guard_refuses_secret_shaped_tokens(token):
    assert cli_module._looks_like_credential(token) is True


@pytest.mark.parametrize(
    "text",
    [
        "a plain sentence someone actually wants read aloud",
        "hello",  # short single word
        "supercalifragilistic",  # long but letters-only
        "12345678901234567890123",  # digits-only (a phone number)
        "a1a1a1a1a1a1a1a1a1a1a1a1",  # mixed but low entropy
        "https://example.com/reports/x1y2z3w4v5u6t7s8",  # a URL
        "",
        "   \n  ",
    ],
)
def test_credential_guard_allows_ordinary_text(text):
    assert cli_module._looks_like_credential(text) is False


# --- vocalize clip ----------------------------------------------------------


def test_clip_speaks_the_clipboard(monkeypatch, tmp_path):
    _played, captured_text, _settings = _patch_tts(monkeypatch)
    monkeypatch.setattr(cli_module, "read_clipboard", lambda: "copied words to speak")
    out_file = tmp_path / "out.mp3"

    result = CliRunner().invoke(
        main, ["clip", "--api-key", "fake-key", "--output", str(out_file), "--no-play"]
    )

    assert result.exit_code == 0, result.output
    assert captured_text == ["copied words to speak"]


def test_clip_stops_current_playback_first(monkeypatch, tmp_path):
    _patch_tts(monkeypatch)
    monkeypatch.setattr(cli_module, "read_clipboard", lambda: "new content")
    stopped = {}
    monkeypatch.setattr(cli_module, "stop_playback", lambda: stopped.setdefault("hit", True))
    out_file = tmp_path / "out.mp3"

    result = CliRunner().invoke(
        main, ["clip", "--api-key", "fake-key", "--output", str(out_file)]
    )

    assert result.exit_code == 0, result.output
    assert stopped == {"hit": True}


def test_clip_does_not_stop_playback_with_no_play(monkeypatch, tmp_path):
    _patch_tts(monkeypatch)
    monkeypatch.setattr(cli_module, "read_clipboard", lambda: "new content")

    def boom():
        raise AssertionError("stop_playback called for a --no-play run")

    monkeypatch.setattr(cli_module, "stop_playback", boom)
    out_file = tmp_path / "out.mp3"

    result = CliRunner().invoke(
        main, ["clip", "--api-key", "fake-key", "--output", str(out_file), "--no-play"]
    )

    assert result.exit_code == 0, result.output


def test_clip_refuses_an_empty_clipboard(monkeypatch):
    _played, captured_text, _settings = _patch_tts(monkeypatch)
    monkeypatch.setattr(cli_module, "read_clipboard", lambda: "   \n ")

    result = CliRunner().invoke(main, ["clip", "--api-key", "fake-key", "--no-play"])

    assert result.exit_code != 0
    assert isinstance(result.exception, cli_module.TTSRequestError)
    assert captured_text == []


def test_clip_refuses_a_credential_without_echoing_it(monkeypatch):
    _played, captured_text, _settings = _patch_tts(monkeypatch)
    secret = "sk-supersecretvalue123456"
    monkeypatch.setattr(cli_module, "read_clipboard", lambda: secret)

    result = CliRunner().invoke(main, ["clip", "--api-key", "fake-key", "--no-play"])

    assert result.exit_code != 0
    assert "Refusing to speak" in result.output
    # The whole point: nothing secret in the transcript, nothing to the API.
    assert secret not in result.output
    assert "supersecret" not in result.output
    assert captured_text == []


def test_clip_allow_secret_bypasses_the_guard(monkeypatch, tmp_path):
    _played, captured_text, _settings = _patch_tts(monkeypatch)
    monkeypatch.setattr(cli_module, "read_clipboard", lambda: "sk-fine-really")
    out_file = tmp_path / "out.mp3"

    result = CliRunner().invoke(
        main,
        ["clip", "--allow-secret", "--api-key", "fake-key",
         "--output", str(out_file), "--no-play"],
    )

    assert result.exit_code == 0, result.output
    assert captured_text == ["sk-fine-really"]


# --- the --ask-dialog fallback ----------------------------------------------


def _fake_osascript(monkeypatch, returncode=0, stdout="", raise_exc=None):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if raise_exc is not None:
            raise raise_exc
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    return calls


def test_dialog_truncate_button_maps_to_truncate(monkeypatch):
    calls = _fake_osascript(monkeypatch, stdout="button returned:Truncate, gave up:false")
    assert cli_module._ask_to_truncate_dialog(5000, 100) == "truncate"
    assert calls[0][0] == "osascript"
    # Only vocalize's own integers reach the AppleScript source.
    assert "5,000" in calls[0][2]
    assert "100" in calls[0][2]


def test_dialog_speak_all_button_maps_to_all(monkeypatch):
    _fake_osascript(monkeypatch, stdout="button returned:Speak all, gave up:false")
    assert cli_module._ask_to_truncate_dialog(5000, 100) == "all"


def test_dialog_cancel_maps_to_cancel(monkeypatch):
    # Cancel/Esc raises AppleScript error -128, surfacing as a non-zero exit.
    _fake_osascript(monkeypatch, returncode=1)
    assert cli_module._ask_to_truncate_dialog(5000, 100) == "cancel"


def test_dialog_timeout_takes_the_default_truncate(monkeypatch):
    # A given-up dialog exits 0 with an EMPTY button name — the real macOS
    # output is "button returned:, gave up:true" (verified live). The
    # not-"Speak all" fallback must map that to truncate.
    _fake_osascript(monkeypatch, stdout="button returned:, gave up:true")
    assert cli_module._ask_to_truncate_dialog(5000, 100) == "truncate"


def test_dialog_subprocess_failure_fails_closed(monkeypatch):
    _fake_osascript(monkeypatch, raise_exc=OSError("no osascript"))
    assert cli_module._ask_to_truncate_dialog(5000, 100) == "cancel"


def test_ask_dialog_speak_all_skips_the_cap(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_module, "_ask_to_truncate", lambda n, cap: None)
    monkeypatch.setattr(cli_module, "_ask_to_truncate_dialog", lambda n, cap: "all")

    result, captured = _speak_long(
        monkeypatch, tmp_path, ["--max-chars", "50", "--overflow", "ask", "--ask-dialog"]
    )

    assert result.exit_code == 0, result.output
    assert len(captured[0]) > 50


def test_ask_dialog_truncate_applies_the_cap(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_module, "_ask_to_truncate", lambda n, cap: None)
    monkeypatch.setattr(cli_module, "_ask_to_truncate_dialog", lambda n, cap: "truncate")

    result, captured = _speak_long(
        monkeypatch, tmp_path, ["--max-chars", "50", "--overflow", "ask", "--ask-dialog"]
    )

    assert result.exit_code == 0, result.output
    assert len(captured[0]) <= 50


def test_ask_dialog_cancel_speaks_nothing(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_module, "_ask_to_truncate", lambda n, cap: None)
    monkeypatch.setattr(cli_module, "_ask_to_truncate_dialog", lambda n, cap: "cancel")

    result, captured = _speak_long(
        monkeypatch, tmp_path, ["--max-chars", "50", "--overflow", "ask", "--ask-dialog"]
    )

    assert result.exit_code == 0, result.output
    assert captured == []
    assert "cancelled" in result.output


def test_no_dialog_without_the_flag(monkeypatch, tmp_path):
    # The Stop hook's silent degrade depends on this: overflow "ask" with no
    # tty and no --ask-dialog must never pop a dialog.
    _isolate_overflow_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_module, "_ask_to_truncate", lambda n, cap: None)

    def boom(n, cap):
        raise AssertionError("dialog fired without --ask-dialog")

    monkeypatch.setattr(cli_module, "_ask_to_truncate_dialog", boom)

    result, _captured = _speak_long(
        monkeypatch, tmp_path, ["--max-chars", "50", "--overflow", "ask"]
    )

    assert result.exit_code == 0, result.output
    assert "no terminal to ask on" in result.output


# --- the provider chain -----------------------------------------------------


def test_provider_flag_forces_a_single_provider_chain(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    calls = []
    _patch_tts(monkeypatch, calls=calls)

    result = CliRunner().invoke(
        main,
        ["speak", "hello", "--provider", "google", "--output", str(tmp_path / "out.mp3"),
         "--no-play"],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["chain"] == ["google"]


def test_no_provider_flag_leaves_the_configured_chain_alone(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.delenv("VOCALIZE_CHAIN", raising=False)
    calls = []
    _patch_tts(monkeypatch, calls=calls)

    result = CliRunner().invoke(
        main, ["speak", "hello", "--output", str(tmp_path / "out.mp3"), "--no-play"]
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["chain"] == ["elevenlabs", "say"]


def test_api_key_with_another_provider_is_a_usage_error(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    calls = []
    _patch_tts(monkeypatch, calls=calls)

    result = CliRunner().invoke(
        main, ["speak", "hello", "--api-key", "sk-secret", "--provider", "openai", "--no-play"]
    )

    assert result.exit_code == 2
    assert "--api-key only applies to ElevenLabs" in result.output
    assert "vocalize auth login --provider openai" in result.output
    # Refused before anything was synthesized, and the key is not echoed.
    assert calls == []
    assert "sk-secret" not in result.output


def test_an_unknown_provider_is_rejected_at_the_flag_layer(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    _patch_tts(monkeypatch)

    result = CliRunner().invoke(main, ["speak", "hello", "--provider", "nope", "--no-play"])

    assert result.exit_code == 2
    assert "--provider" in result.output


def test_clip_takes_the_provider_flag_too(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    calls = []
    _patch_tts(monkeypatch, calls=calls)
    monkeypatch.setattr(cli_module, "read_clipboard", lambda: "copied words")

    result = CliRunner().invoke(
        main, ["clip", "--provider", "say", "--output", str(tmp_path / "out.mp3"), "--no-play"]
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["chain"] == ["say"]


def test_the_output_extension_follows_the_provider_that_answered(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "DEFAULT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cli_module, "play_audio", lambda path: None)
    monkeypatch.setattr(
        cli_module, "chain_run", lambda text, **kwargs: (b"m4a-bytes", "say", "m4a")
    )

    result = CliRunner().invoke(main, ["speak", "hello", "--no-play"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "last.m4a").read_bytes() == b"m4a-bytes"


def test_a_stopped_read_exits_cleanly(monkeypatch, tmp_path):
    from vocalize.exceptions import PlaybackStopped

    def stopped(text, **kwargs):
        raise PlaybackStopped("Playback stopped.")

    monkeypatch.setattr(cli_module, "chain_run", stopped)
    monkeypatch.setattr(cli_module, "play_audio", lambda path: None)
    monkeypatch.setattr(cli_module, "play_sequence", lambda paths, **kwargs: True)

    result = CliRunner().invoke(main, ["speak", "hello", "--output", str(tmp_path / "out.mp3")])

    assert result.exit_code == 0, result.output
    assert "Stopped." in result.output
    assert not (tmp_path / "out.mp3").exists()


def _patch_streaming(monkeypatch, tmp_path, pieces=("one", "two", "three")):
    """A chain_run that streams `pieces` the way a STREAMING provider does."""
    played = []
    monkeypatch.setattr(cli_module, "play_audio", lambda path: played.append(("whole", path)))

    def fake_play_sequence(paths, **kwargs):
        played.extend(("piece", Path(p).read_bytes()) for p in paths)
        return True

    monkeypatch.setattr(cli_module, "play_sequence", fake_play_sequence)

    def fake_chain_run(text, **kwargs):
        source = tmp_path / "chain-tmp"
        source.mkdir(exist_ok=True)
        for index, piece in enumerate(pieces, start=1):
            path = source / f"{index}.wav"
            path.write_bytes(piece.encode())
            kwargs["on_chunk"](path)
        return b"".join(p.encode() for p in pieces), "kokoro", "wav"

    monkeypatch.setattr(cli_module, "chain_run", fake_chain_run)
    return played


def test_streaming_plays_each_piece_once_and_never_replays_the_whole_file(
    monkeypatch, tmp_path
):
    played = _patch_streaming(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        main, ["speak", "hello", "--output", str(tmp_path / "out.wav")]
    )

    assert result.exit_code == 0, result.output
    assert played == [("piece", b"one"), ("piece", b"two"), ("piece", b"three")]
    assert (tmp_path / "out.wav").read_bytes() == b"onetwothree"


def test_a_streamed_piece_survives_the_chains_temporary_directory(monkeypatch, tmp_path):
    # The chain deletes its temp dir the moment run() returns, while the
    # last piece is usually still playing — the CLI has to own its copy.
    monkeypatch.setattr(cli_module, "play_sequence", lambda paths, **kwargs: True)
    player = cli_module._StreamPlayer(tmp_path)

    with tempfile.TemporaryDirectory() as source:
        piece = Path(source) / "1.wav"
        piece.write_bytes(b"piece")
        assert player.on_chunk(piece) is True

    player.close()

    assert (tmp_path / "1.wav").read_bytes() == b"piece"


def test_a_player_that_blows_up_is_reported_instead_of_hanging(monkeypatch, tmp_path):
    # A failing player used to be fatal in the worst way: the thread died,
    # and the render loop blocked forever on the next handover.
    from vocalize.exceptions import AudioPlaybackError

    def boom(paths, **kwargs):
        raise AudioPlaybackError("afplay failed to play the audio")

    monkeypatch.setattr(cli_module, "play_sequence", boom)
    monkeypatch.setattr(cli_module, "play_audio", lambda path: None)

    def fake_chain_run(text, **kwargs):
        source = tmp_path / "chain-tmp"
        source.mkdir(exist_ok=True)
        rendered = b""
        for index in range(1, 4):
            path = source / f"{index}.wav"
            path.write_bytes(b"piece")
            rendered += b"piece"
            if kwargs["on_chunk"](path) is False:
                from vocalize.exceptions import PlaybackStopped

                # What the real chain does: the stop carries everything
                # rendered up to it.
                raise PlaybackStopped("Playback stopped.", rendered, "wav")
        return b"pieces", "kokoro", "wav"

    monkeypatch.setattr(cli_module, "chain_run", fake_chain_run)

    result = CliRunner().invoke(main, ["speak", "hello", "--output", str(tmp_path / "out.wav")])

    assert result.exit_code != 0
    assert isinstance(result.exception, AudioPlaybackError)
    assert "afplay failed" in str(result.exception)
    # Only playback broke. The audio was rendered and paid for, so it is
    # on disk rather than thrown away with the exception.
    # (how many pieces rendered before the player thread reported in is a
    # race; that anything was saved at all is the point.)
    assert (tmp_path / "out.wav").read_bytes().startswith(b"piece")
    assert "Saved audio to" in result.stderr


def test_forcing_a_provider_tells_the_chain_fallback_is_off(monkeypatch, tmp_path):
    # chain.run needs this to stop advising a fallback the flag disabled.
    calls = []
    _patch_tts(monkeypatch, calls=calls)

    result = CliRunner().invoke(
        main, ["speak", "hello", "--provider", "say", "--no-play",
               "--output", str(tmp_path / "out.mp3")]
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["forced"] is True


def test_without_the_flag_the_chain_is_not_told_it_was_forced(monkeypatch, tmp_path):
    calls = []
    _patch_tts(monkeypatch, calls=calls)

    result = CliRunner().invoke(
        main, ["speak", "hello", "--api-key", "k", "--no-play",
               "--output", str(tmp_path / "out.mp3")]
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["forced"] is False


def test_no_play_never_streams(monkeypatch, tmp_path):
    calls = []
    _patch_tts(monkeypatch, calls=calls)

    result = CliRunner().invoke(
        main, ["speak", "hello", "--output", str(tmp_path / "out.mp3"), "--no-play"]
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["on_chunk"] is None


# --- per-provider `auth login` -----------------------------------------------


def test_auth_login_stores_a_key_for_openai(fake_keychain, monkeypatch):
    import vocalize.auth as auth_module

    class _Stub:
        def validate(self, key):
            pass

    monkeypatch.setattr("vocalize.providers.get", lambda name: _Stub())

    result = CliRunner().invoke(
        main,
        ["auth", "login", "--provider", "openai", "--stdin"],
        input="sk-openaikeyabc1234567\n",
    )

    assert result.exit_code == 0, result.output
    assert fake_keychain[(auth_module.SERVICE, "openai-api-key")] == "sk-openaikeyabc1234567"
    assert "openai-api-key" in result.output
    assert "sk-openaikeyabc1234567" not in result.output


def test_auth_login_refuses_polly(fake_keychain):
    result = CliRunner().invoke(main, ["auth", "login", "--provider", "polly"])

    assert result.exit_code == 1
    assert "Polly uses your AWS credentials" in result.output
    assert "vocalize auth status --provider polly" in result.output


@pytest.mark.parametrize("provider", ["say", "kokoro"])
def test_auth_login_refuses_local_providers(fake_keychain, provider):
    result = CliRunner().invoke(main, ["auth", "login", "--provider", provider])

    assert result.exit_code == 1
    assert "is local and needs no credentials" in result.output


# --- per-provider `auth status` ----------------------------------------------


def test_auth_status_no_flag_lists_other_chain_providers(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    result = CliRunner().invoke(main, ["auth", "status"])

    assert result.exit_code == 0, result.output
    assert "say: local, no credentials" in result.output


def test_auth_status_provider_google_environment(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key")

    result = CliRunner().invoke(main, ["auth", "status", "--provider", "google"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "google: environment"


def test_auth_status_provider_openai_not_configured(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = CliRunner().invoke(main, ["auth", "status", "--provider", "openai"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "openai: not configured"


def test_auth_status_provider_admits_an_unreadable_keychain(monkeypatch):
    # key_source flattens "could not look" into "not found"; the status
    # line used to repeat that as "not configured" and send the user off
    # to store a key they may already have.
    class _Broken:
        def get_password(self, service, username):
            raise RuntimeError("no recommended backend")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("vocalize.auth._backend", lambda: _Broken())

    result = CliRunner().invoke(main, ["auth", "status", "--provider", "openai"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "openai: keychain unavailable (no recommended backend)"


def test_auth_status_provider_openai_keychain_is_masked(fake_keychain, monkeypatch):
    import vocalize.auth as auth_module

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake_keychain[(auth_module.SERVICE, "openai-api-key")] = "sk-abcxyz1234567890"

    result = CliRunner().invoke(main, ["auth", "status", "--provider", "openai"])

    assert result.exit_code == 0, result.output
    assert "openai: keychain (sk-a…)" in result.output
    assert "sk-abcxyz1234567890" not in result.output


def test_auth_status_provider_polly(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "shh")

    result = CliRunner().invoke(main, ["auth", "status", "--provider", "polly"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "polly: environment"


def test_auth_status_provider_say_and_kokoro(monkeypatch):
    result_say = CliRunner().invoke(main, ["auth", "status", "--provider", "say"])
    result_kokoro = CliRunner().invoke(main, ["auth", "status", "--provider", "kokoro"])

    assert result_say.output.strip() == "say: local, no credentials"
    assert result_kokoro.output.strip() == "kokoro: local provider (see: vocalize local status)"


# --- `voices --provider` -----------------------------------------------------


def test_voices_provider_google_dispatches_to_the_provider_module(monkeypatch):
    class _Stub:
        def list_voices(self):
            return [{"id": "en-US-Neural2-F", "name": "Neural2 F (en-US)"}]

    monkeypatch.setattr(cli_module.providers, "get", lambda name: _Stub())

    result = CliRunner().invoke(main, ["voices", "--provider", "google"])

    assert result.exit_code == 0, result.output
    assert "en-US-Neural2-F\tNeural2 F (en-US)" in result.output


def test_voices_api_key_with_another_provider_is_a_usage_error(monkeypatch):
    result = CliRunner().invoke(main, ["voices", "--api-key", "x", "--provider", "openai"])

    assert result.exit_code == 2
    assert "--api-key only applies to ElevenLabs" in result.output


def test_voices_kokoro_lists_the_manifest_ids_without_the_runtime(monkeypatch):
    # The voice list is static (manifest), so it works before `local install`
    # and never spawns the worker.
    monkeypatch.setattr(
        cli_module.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("worker spawned")),
    )
    result = CliRunner().invoke(main, ["voices", "--provider", "kokoro"])

    assert result.exit_code == 0, result.output
    assert "af_heart\taf_heart" in result.output
    assert len(result.output.strip().splitlines()) == 54


# --- `usage` ------------------------------------------------------------------


def test_usage_table_shows_budget_and_marks_exhausted(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "build_client", lambda key: object())
    monkeypatch.setattr(
        cli_module,
        "get_usage",
        lambda client: {"tier": "free", "used": 0, "limit": 10000, "resets_at": None},
    )
    monkeypatch.setattr(cli_module, "DEFAULT_CACHE_DIR", tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfg = tmp_path / "cfg" / "vocalize" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[providers.google]\nmonthly_chars = 1000\n", encoding="utf-8")

    from vocalize import ledger

    ledger.record("google", 1200)

    result = CliRunner().invoke(main, ["usage", "--api-key", "fake-key"])

    assert result.exit_code == 0, result.output
    assert "google: 1,200 / 1,000 bytes (120.0%) EXHAUSTED" in result.output
    assert "elevenlabs: 0 characters (no monthly_chars set — unlimited)" in result.output


def test_usage_marks_an_exhausted_provider_without_a_budget(monkeypatch, tmp_path):
    # The common case: no monthly_chars set. A provider a real quota error
    # marked exhausted was invisible on this line.
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(cli_module, "DEFAULT_CACHE_DIR", tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))

    from vocalize import ledger

    ledger.mark_exhausted("elevenlabs")

    result = CliRunner().invoke(main, ["usage"])

    assert result.exit_code == 0, result.output
    assert "elevenlabs: 0 characters (no monthly_chars set — unlimited) EXHAUSTED" in result.output
    assert "google: 0 bytes (no monthly_chars set — unlimited)\n" in result.output


def test_usage_without_a_key_skips_the_elevenlabs_block(monkeypatch, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(cli_module, "DEFAULT_CACHE_DIR", tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))

    result = CliRunner().invoke(main, ["usage"])

    assert result.exit_code == 0, result.output
    assert "ElevenLabs remote quota: no key configured, skipped." in result.output


# --- `local install` resume ---------------------------------------------------


def test_local_install_never_re_downloads_a_verified_file(monkeypatch, tmp_path):
    # A partial install leaves one good file on disk; re-running it used to
    # re-fetch all 326 MB regardless.
    from vocalize.local import install as install_module
    from vocalize.local import kokoro_manifest as manifest
    from vocalize.providers import kokoro as kokoro_provider

    monkeypatch.setattr(manifest, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(kokoro_provider, "uv_path", lambda: "/usr/local/bin/uv")
    monkeypatch.setattr(install_module, "selftest", lambda uv, **kw: "ok")

    done, missing = manifest.FILES[0], manifest.FILES[1]
    monkeypatch.setattr(
        install_module, "file_is_verified",
        lambda entry, model_dir=None: entry["name"] == done["name"],
    )

    requested = []

    def fake_download(url, dest, size, sha256, progress=None):
        requested.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"downloaded")
        return dest

    monkeypatch.setattr(install_module, "download_file", fake_download)

    result = CliRunner().invoke(main, ["local", "install", "--yes"])

    assert result.exit_code == 0, result.output
    assert requested == [missing["url"]]
    assert done["url"] not in requested
    assert f"{done['name']}: already verified, skipping" in result.output


def test_local_install_still_downloads_a_file_that_fails_verification(monkeypatch, tmp_path):
    # The skip must never become "a file is on disk, so trust it": a
    # corrupted or tampered file has to be fetched again, not adopted.
    import hashlib

    from vocalize.local import install as install_module
    from vocalize.local import kokoro_manifest as manifest
    from vocalize.providers import kokoro as kokoro_provider

    payloads = [b"real-model-bytes", b"real-voice-bytes"]
    files = [
        {
            "name": entry["name"],
            "url": entry["url"],
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for entry, payload in zip(manifest.FILES, payloads)
    ]
    model_dir = tmp_path / "models"
    monkeypatch.setattr(manifest, "FILES", files)
    monkeypatch.setattr(manifest, "MODEL_DIR", model_dir)
    monkeypatch.setattr(kokoro_provider, "uv_path", lambda: "/usr/local/bin/uv")
    monkeypatch.setattr(install_module, "selftest", lambda uv, **kw: "ok")

    # Right name, right size, wrong bytes — the shape a tampered or
    # truncated-then-padded file has.
    model_dir.mkdir(parents=True)
    (model_dir / files[0]["name"]).write_bytes(b"evil-model-bytes")

    requested = []

    def fake_download(url, dest, size, sha256, progress=None):
        requested.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payloads[[f["url"] for f in files].index(url)])
        return dest

    monkeypatch.setattr(install_module, "download_file", fake_download)

    result = CliRunner().invoke(main, ["local", "install", "--yes"])

    assert result.exit_code == 0, result.output
    assert requested == [files[0]["url"], files[1]["url"]]
    assert "already verified, skipping" not in result.output


# --- `settings` chain= line ---------------------------------------------------


def test_settings_prints_the_resolved_chain(monkeypatch, tmp_path):
    _isolate_overflow_env(monkeypatch, tmp_path)

    result = CliRunner().invoke(main, ["settings"])

    assert result.exit_code == 0, result.output
    assert "chain=elevenlabs,say" in result.output


# --- `vocalize chain` ---------------------------------------------------------


def test_chain_show_prints_order_and_source(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.delenv("VOCALIZE_CHAIN", raising=False)

    result = CliRunner().invoke(main, ["chain"])

    assert result.exit_code == 0, result.output
    assert "chain=elevenlabs,say" in result.output
    assert "source=default" in result.output


def test_chain_write_preserves_other_keys_and_tables(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_path = tmp_path / "vocalize" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        'voice = "abc"\n\n[providers.google]\nvoice = "en-US-Neural2-F"\nmonthly_chars = 500\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["chain", "google", "say"])

    assert result.exit_code == 0, result.output
    assert "chain=google,say" in result.output
    assert f"wrote {cfg_path}" in result.output

    text = cfg_path.read_text()
    assert 'chain = ["google", "say"]' in text
    assert 'voice = "abc"' in text
    assert "[providers.google]" in text
    assert "monthly_chars = 500" in text


def test_chain_creates_the_file_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_path = tmp_path / "vocalize" / "config.toml"
    assert not cfg_path.exists()

    result = CliRunner().invoke(main, ["chain", "polly", "say"])

    assert result.exit_code == 0, result.output
    assert cfg_path.exists()
    assert 'chain = ["polly", "say"]' in cfg_path.read_text()


def test_chain_rejects_an_unknown_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = CliRunner().invoke(main, ["chain", "nope"])

    assert result.exit_code == 2
    assert "Unknown provider" in result.output
    assert "elevenlabs" in result.output


def test_chain_rejects_a_duplicate_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = CliRunner().invoke(main, ["chain", "say", "say"])

    assert result.exit_code == 2
    assert "Duplicate" in result.output
