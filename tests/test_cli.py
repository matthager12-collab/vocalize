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
    assert len(captured_text[0]) <= 10 + len("... (truncated)")
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
