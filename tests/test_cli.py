from pathlib import Path

import pytest
from click.testing import CliRunner

import vocalize.cli as cli_module
from vocalize.cli import main


def _patch_tts(monkeypatch, audio=b"fake-mp3-bytes"):
    monkeypatch.setattr(cli_module, "build_client", lambda key: object())
    monkeypatch.setattr(cli_module, "synthesize", lambda client, text, settings: audio)
    played = {}
    monkeypatch.setattr(cli_module, "play_audio", lambda path: played.setdefault("path", path))
    return played


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
    played = _patch_tts(monkeypatch)
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
    _patch_tts(monkeypatch)
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
