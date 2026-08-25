from pathlib import Path

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
