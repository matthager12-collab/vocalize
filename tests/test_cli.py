import builtins
import io
import subprocess

import pytest
from click.testing import CliRunner

import vocalize.cli as cli_module
from vocalize.cli import main


def _patch_tts(monkeypatch, audio=b"fake-mp3-bytes"):
    monkeypatch.setattr(cli_module, "build_client", lambda key: object())
    captured_text = []
    captured_settings = []

    def fake_synthesize(client, text, settings):
        captured_text.append(text)
        captured_settings.append(settings)
        return audio

    monkeypatch.setattr(cli_module, "synthesize", fake_synthesize)
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


def test_short_input_makes_one_convert_call_with_unchanged_message(monkeypatch, tmp_path):
    _played, captured_text, _captured_settings = _patch_tts(monkeypatch)
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["speak", "hello world", "--api-key", "fake-key", "--output", str(out_file), "--no-play"],
    )

    assert result.exit_code == 0, result.output
    # Exactly one convert call, and the message is the pre-chunking wording
    # — a single-chunk run must be byte-identical to before chunking existed.
    assert captured_text == ["hello world"]
    assert "Requesting 11 characters of audio from ElevenLabs..." in result.output
    assert "Long input" not in result.output
    assert "Requesting chunk" not in result.output


def test_long_input_splits_into_chunks_and_concatenates_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "build_client", lambda key: object())
    calls = []

    def fake_synthesize(client, text, settings):
        calls.append(text)
        # Distinct, order-dependent bytes per call, so concatenation order
        # in the saved file is actually being checked, not just its length.
        return f"[chunk {len(calls)}: {text}]".encode()

    monkeypatch.setattr(cli_module, "synthesize", fake_synthesize)
    monkeypatch.setattr(cli_module, "play_audio", lambda path: None)

    text = "First sentence here. " * 20
    out_file = tmp_path / "out.mp3"
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "speak", text, "--api-key", "fake-key",
            "--output", str(out_file), "--no-play", "--chunk-chars", "50",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) > 1
    expected = b"".join(f"[chunk {i}: {c}]".encode() for i, c in enumerate(calls, start=1))
    assert out_file.read_bytes() == expected
    assert f"Long input: splitting into {len(calls)} chunks." in result.output
    assert f"Requesting chunk 1/{len(calls)}" in result.output
    assert all(len(c) <= 50 for c in calls)


def test_missing_api_key_gives_clean_error(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(cli_module, "build_client", lambda key: object())
    monkeypatch.setattr(cli_module, "synthesize", lambda client, text, settings: b"x")
    runner = CliRunner()

    result = runner.invoke(main, ["speak", "hello", "--no-play"])

    assert result.exit_code != 0
    from vocalize.exceptions import MissingAPIKeyError

    assert isinstance(result.exception, MissingAPIKeyError)


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
