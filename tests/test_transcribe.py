"""Tests for `vocalize transcribe` and its hidden `notes` alias (T-12).

The worker is faked; no model, audio device or network is touched.
"""

from __future__ import annotations

import json
import stat
import subprocess
import wave
from pathlib import Path

import pytest
from click.testing import CliRunner

from vocalize import __version__, notes
from vocalize.cli import main

# design.md § Contracts, Transcription: the frontmatter keys notes wrote.
FRONTMATTER_KEYS = (
    "title", "date", "type", "captured", "license", "topics", "source",
    "source_sha256", "duration_seconds", "template", "transcribed_by",
    "summarized_by", "left_machine", "trust", "vocalize",
)


def _write_wav(path: Path, seconds: float = 2.0, *, silent: bool = False) -> None:
    """16 kHz mono 16-bit; audible (+1000, -1000 pairs) unless `silent`."""
    frames = int(16000 * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * frames if silent else b"\xe8\x03\x18\xfc" * (frames // 2))


def _is_silent_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as w:
            return not any(w.readframes(w.getnframes()))
    except (OSError, wave.Error, EOFError):
        return False


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n")
    head = text.split("---\n", 2)[1]
    return dict(line.split(": ", 1) for line in head.splitlines())


@pytest.fixture
def env(tmp_path, monkeypatch):
    folder = tmp_path / "transcripts"
    folder.mkdir(mode=0o700)
    home = tmp_path / "config-home"
    (home / "vocalize").mkdir(parents=True)
    (home / "vocalize" / "config.toml").write_text(
        f"[notes]\nfolder = {json.dumps(str(folder))}\n", encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))

    from vocalize import local as local_pkg
    from vocalize.local import install
    monkeypatch.setattr(install, "installed", lambda manifest, **kw: (True, "ready"))
    monkeypatch.setattr(local_pkg, "uv_path", lambda: "/usr/bin/true")

    def fake_run(cmd, **kw):
        assert cmd[0] == "afconvert", cmd
        _write_wav(Path(cmd[-1]), silent=_is_silent_wav(Path(cmd[-2])))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    calls = []

    def fake_worker(cmd, duration, *, env=None):
        calls.append(cmd)
        return "Hello world", [
            {"start": 0.0, "end": 1.0, "text": "Hello"},
            {"start": 65.0, "end": 66.0, "text": "world"},
        ]

    monkeypatch.setattr(notes, "_transcribe_long", fake_worker)
    src = tmp_path / "Synthetic Take.m4a"
    _write_wav(src)
    return {"folder": folder, "src": src, "calls": calls}


def test_transcribe_writes_note_with_every_frontmatter_key(env):
    result = CliRunner().invoke(main, ["transcribe", str(env["src"])])
    assert result.exit_code == 0, result.output
    written = sorted(env["folder"].glob("*.md"))
    assert len(written) == 1
    assert written[0].name.endswith("-synthetic-take.md")
    assert stat.S_IMODE(written[0].stat().st_mode) == 0o600
    text = written[0].read_text(encoding="utf-8")
    fm = _frontmatter(text)
    assert tuple(fm) == FRONTMATTER_KEYS
    assert fm["template"] == '"none"'
    assert fm["summarized_by"] == '"none"'
    assert fm["left_machine"] == "false"
    assert fm["trust"] == '"untrusted-transcript"'
    assert fm["vocalize"] == f'"{__version__}"'
    assert fm["source"] == f'"{env["src"].resolve()}"'
    assert fm["duration_seconds"] == "2"
    body = text.split("---\n", 2)[2]
    assert body == "\n## Transcript\n\n[00:00] Hello\n[01:05] world\n"
    assert "summary" not in body.lower()
    assert len(env["calls"]) == 1


def test_second_run_skips_already_processed_source(env):
    runner = CliRunner()
    assert runner.invoke(main, ["transcribe", str(env["src"])]).exit_code == 0
    again = runner.invoke(main, ["transcribe", str(env["src"])])
    assert again.exit_code == 0, again.output
    assert "Skipping Synthetic Take.m4a: already processed" in again.output
    assert len(list(env["folder"].glob("*.md"))) == 1
    assert len(env["calls"]) == 1


def test_note_written_by_an_older_version_is_recognised(env):
    """A folder a 0.15 `notes` run processed is not transcribed again."""
    src = env["src"].resolve()
    old = env["folder"] / "2026-01-01-synthetic-take.md"
    old.write_text(
        "---\n"
        f'title: "Synthetic Take"\nsource: "{src}"\n'
        f'source_sha256: "{notes._sha256_file(src)}"\ntemplate: "memo"\n'
        'summarized_by: "local:qwen3.5-4b"\n---\n\nOld summary\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(main, ["transcribe", str(env["src"])])
    assert result.exit_code == 0, result.output
    assert "already processed as 2026-01-01-synthetic-take.md" in result.output
    assert env["calls"] == []


@pytest.mark.parametrize("extra", [["--summarizer", "off"], ["--template", "memo"]])
def test_notes_alias_behaves_like_transcribe(env, tmp_path, extra):
    alias = CliRunner().invoke(main, ["notes", str(env["src"]), *extra])
    assert alias.exit_code == 0, alias.output
    [alias_note] = env["folder"].glob("*.md")
    alias_text = alias_note.read_text(encoding="utf-8")
    alias_note.unlink()

    direct = CliRunner().invoke(main, ["transcribe", str(env["src"])])
    assert direct.exit_code == 0, direct.output
    [direct_note] = env["folder"].glob("*.md")
    assert alias_note.name == direct_note.name
    assert alias_text == direct_note.read_text(encoding="utf-8")
    assert _frontmatter(alias_text)["template"] == '"none"'


def test_notes_alias_warns_once_when_a_summary_is_requested(env):
    runner = CliRunner()
    quiet = runner.invoke(main, ["notes", str(env["src"]), "--summarizer", "off"])
    assert quiet.exit_code == 0, quiet.output
    assert "summaries" not in quiet.stderr
    for note in env["folder"].glob("*.md"):
        note.unlink()

    loud = runner.invoke(main, ["notes", str(env["src"]), "--summarizer", "local",
                                "--template", "meeting"])
    assert loud.exit_code == 0, loud.output
    lines = [line for line in loud.stderr.splitlines() if "summar" in line.lower()]
    assert len(lines) == 1
    assert "removed" in lines[0] and "transcript" in lines[0]
    assert len(list(env["folder"].glob("*.md"))) == 1


def test_notes_alias_is_hidden_and_still_has_help():
    top = CliRunner().invoke(main, ["--help"])
    assert top.exit_code == 0
    assert "transcribe" in top.output
    assert "notes" not in top.output
    assert "summarize-text" not in top.output
    assert CliRunner().invoke(main, ["notes", "--help"]).exit_code == 0


def test_summarize_text_command_is_gone():
    result = CliRunner().invoke(main, ["summarize-text"], input="private text")
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_settings_prints_one_fixed_notes_line(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = tmp_path / "vocalize" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[notes]\nfolder = "~/Synthetic"\n', encoding="utf-8")
    result = CliRunner().invoke(main, ["settings"])
    assert result.exit_code == 0, result.output
    notes_lines = [line for line in result.output.splitlines() if line.startswith("notes.")]
    assert notes_lines == ["notes.summarizer=off"]


def test_text_source_is_transcribed_by_file(env, tmp_path):
    src = tmp_path / "words.txt"
    src.write_text("Plain synthetic words", encoding="utf-8")
    result = CliRunner().invoke(main, ["transcribe", str(src)])
    assert result.exit_code == 0, result.output
    [note] = env["folder"].glob("*.md")
    text = note.read_text(encoding="utf-8")
    assert _frontmatter(text)["transcribed_by"] == '"file"'
    assert text.endswith("\n## Transcript\n\nPlain synthetic words\n")


def test_a_silent_recording_exits_0_with_a_silent_note_and_is_skipped_next_time(env, tmp_path):
    silence = tmp_path / "Silence.m4a"
    _write_wav(silence, 10.0, silent=True)
    result = CliRunner().invoke(main, ["transcribe", str(silence)])
    assert result.exit_code == 0, result.output
    assert env["calls"] == []  # the worker never ran
    [note] = env["folder"].glob("*.md")
    text = note.read_text(encoding="utf-8")
    assert set(_frontmatter(text)) == set(FRONTMATTER_KEYS)
    assert text.endswith("\n## Transcript\n\n" + notes.SILENT_TRANSCRIPT + "\n")
    again = CliRunner().invoke(main, ["transcribe", str(silence)])
    assert again.exit_code == 0 and "already processed" in again.output
    assert env["calls"] == [] and list(env["folder"].glob("*.md")) == [note]
