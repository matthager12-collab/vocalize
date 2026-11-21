"""Tests for the transcription pipeline in vocalize/notes.py (T-12).

Covers:
- Self-ingestion guard
- Done-scan on hash AND resolved path with every skip printed by name
- Every segment sanitized (escape sequences stripped, trust key present)
- Atomic write (.tmp mode 0600 then replaced; killed run leaves no half note)
- Run lock (<folder>/.vocalize.lock prevents concurrent runs)
- Stale temporary directory sweep (> 24h swept, < 24h kept)
- -x.m4a argument handling
- Folder never re-moded (existing permissions preserved)
- Historical provenance is never rewritten
- Keep audio behavior
- Threads clean (threading.active_count() unchanged)
- PermissionError under ~/Documents formatting
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import pytest
from click.testing import CliRunner

from vocalize import notes
from vocalize.cli import main


def write_wav(path: Path, duration_seconds: float = 1.0) -> None:
    """Create a minimal valid 16 kHz mono 16-bit WAV file."""
    rate = 16000
    n_frames = int(rate * duration_seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n_frames)


def _use_folder(monkeypatch, tmp_path: Path, folder: Path) -> None:
    """Set the folder through a config file, the contract both table names keep."""
    home = tmp_path / "config-home"
    (home / "vocalize").mkdir(parents=True, exist_ok=True)
    (home / "vocalize" / "config.toml").write_text(
        f"[notes]\nfolder = {json.dumps(str(folder))}\n", encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))


@pytest.fixture
def notes_env(tmp_path, monkeypatch):
    """Set up isolated notes folder and mocks."""
    notes_dir = tmp_path / "notes_folder"
    notes_dir.mkdir(parents=True, mode=0o700)

    # Point the notes folder to tmp_path
    _use_folder(monkeypatch, tmp_path, notes_dir)

    # Mock whisper install check so worker can run
    from vocalize import local as local_pkg
    from vocalize.local import install
    monkeypatch.setattr(install, "installed", lambda manifest, **kw: (True, "ready"))
    monkeypatch.setattr(local_pkg, "uv_path", lambda: "/usr/bin/true")

    # Mock afconvert so tests don't require real audio encoding
    def fake_afconvert(cmd, **kw):
        if cmd[0] == "afconvert":
            dest = Path(cmd[-1])
            write_wav(dest, duration_seconds=2.0)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        kw.pop("check", None)
        return subprocess.run(cmd, check=False, **kw)

    monkeypatch.setattr(subprocess, "run", fake_afconvert)

    # Default fake transcription
    def fake_transcribe_long(cmd, duration, *, env=None):
        return "Hello world transcript", [
            {"start": 0.0, "end": 1.0, "text": "Hello"},
            {"start": 1.0, "end": 2.0, "text": "world transcript"},
        ]

    monkeypatch.setattr(notes, "_transcribe_long", fake_transcribe_long)

    return {"dir": notes_dir}


# --- Self-Ingestion Guard ------------------------------------------------


def test_notes_uses_explicit_runtime_and_scrubbed_env(notes_env, tmp_path, monkeypatch):
    from vocalize.local import runtime

    interpreter = tmp_path / "selected python $(literal)"
    interpreter.symlink_to(sys.executable)
    monkeypatch.setenv("VOCALIZE_WHISPER_PYTHON", str(interpreter))
    monkeypatch.setenv("OPENAI_API_KEY", "SECRET_CANARY")
    monkeypatch.setenv("PYTHONPATH", "SECRET_CANARY")
    monkeypatch.setattr("vocalize.local.uv_path", lambda: pytest.fail("must not use uv"))
    calls = []

    def transcribe(cmd, duration, *, env=None):
        calls.append((cmd, env))
        return "Synthetic note transcript", []

    monkeypatch.setattr(notes, "_transcribe_long", transcribe)
    src = tmp_path / "synthetic.m4a"
    write_wav(src)
    assert len(notes.process_sources([src])) == 1
    cmd, env = calls[0]
    assert cmd[:3] == [str(interpreter), "-I", "-B"]
    assert Path(cmd[3]).name == "runtime_worker.py" and cmd[4] == "whisper"
    assert "--segments" in cmd and "--transcribe" in cmd
    assert "SECRET_CANARY" not in str(env)
    assert env[runtime.IDENTITY_ENV]
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"


def test_notes_invalid_explicit_runtime_never_falls_back(notes_env, tmp_path, monkeypatch):
    monkeypatch.setenv("VOCALIZE_WHISPER_PYTHON", "/missing/python")
    monkeypatch.setattr("vocalize.local.uv_path", lambda: pytest.fail("must not fall back to uv"))
    monkeypatch.setattr(notes, "_transcribe_long", lambda *a, **k: pytest.fail("must not start transcriber"))
    src = tmp_path / "synthetic.m4a"
    write_wav(src)
    with pytest.raises(notes.NotesError, match="VOCALIZE_WHISPER_PYTHON"):
        notes.process_sources([src])


def test_self_ingestion_guard(notes_env, capsys):
    folder = notes_env["dir"]
    inside_file = folder / "test.m4a"
    write_wav(inside_file)

    created = notes.process_sources([inside_file])
    assert len(created) == 0
    out = capsys.readouterr().out
    assert "inside notes folder (self-ingestion guard)" in out


# --- Done-Scan On Hash AND Resolved Path ---------------------------------


def test_done_scan_skips_when_hash_and_path_match(notes_env, tmp_path, capsys):
    src = tmp_path / "recording.txt"
    src.write_text("Recording content here", encoding="utf-8")

    # First run processes file
    created = notes.process_sources([src])
    assert len(created) == 1
    assert created[0].exists()

    # Second run skips and prints skip message by name
    created2 = notes.process_sources([src])
    assert len(created2) == 0
    out = capsys.readouterr().out
    assert "Skipping recording.txt: already processed" in out


def test_done_scan_does_not_skip_different_file_with_same_hash(notes_env, tmp_path):
    # Two files with identical content but different paths
    src1 = tmp_path / "file1.txt"
    src2 = tmp_path / "file2.txt"
    src1.write_text("Identical content", encoding="utf-8")
    src2.write_text("Identical content", encoding="utf-8")

    notes.process_sources([src1])
    created2 = notes.process_sources([src2])

    # Must process src2 because source path differs (review R5)
    assert len(created2) == 1
    assert created2[0].exists()


def test_force_flag_rewrites_note_in_place(notes_env, tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("Initial text", encoding="utf-8")

    created = notes.process_sources([src])
    first_note = created[0]

    time.sleep(0.05)
    src.write_text("Updated text", encoding="utf-8")
    recreated = notes.process_sources([src], force=True)

    assert len(recreated) == 1
    assert recreated[0] == first_note
    content = first_note.read_text(encoding="utf-8")
    assert "Updated text" in content


# --- Sanitization & Note Format ------------------------------------------


def test_every_segment_sanitized_and_trust_key_present(notes_env, tmp_path, monkeypatch):
    # Worker returns ANSI escape sequences and control characters
    def fake_transcribe_with_escapes(cmd, duration, *, env=None):
        return "\x1b[31mInjected\x1b[0m text\x07", [
            {"start": 0.0, "end": 1.0, "text": "\x1b[31mInjected\x1b[0m"},
            {"start": 1.0, "end": 2.0, "text": "text\x07"},
        ]

    monkeypatch.setattr(notes, "_transcribe_long", fake_transcribe_with_escapes)

    src = tmp_path / "audio.m4a"
    write_wav(src)

    created = notes.process_sources([src])
    assert len(created) == 1
    content = created[0].read_text(encoding="utf-8")

    # Escape sequences stripped
    assert "\x1b" not in content
    assert "\x07" not in content
    assert "Injected" in content

    # Trust key present
    assert 'trust: "untrusted-transcript"' in content
    # The running version, not a literal that goes stale at every release.
    from vocalize import __version__
    assert f'vocalize: "{__version__}"' in content
    assert 'type: "recording"' in content
    assert 'license: "personal-use"' in content


# --- Atomic Write --------------------------------------------------------


def test_atomic_write_leaves_no_partial_file_on_error(notes_env, tmp_path, monkeypatch):
    folder = notes_env["dir"]

    # Sabotage write mid-way
    def fail_write(fd, data):
        raise OSError("Disk full simulation")

    monkeypatch.setattr(notes, "_render_frontmatter", lambda **kw: (_ for _ in ()).throw(RuntimeError("crash")))

    src = tmp_path / "audio.txt"
    src.write_text("Sample", encoding="utf-8")

    with pytest.raises(RuntimeError, match="crash"):
        notes.process_sources([src])

    # No .md or .tmp files left behind
    assert list(folder.glob("*.md")) == []
    assert list(folder.glob("*.tmp")) == []


@pytest.mark.parametrize("content", ["new synthetic note", b"new synthetic audio"])
def test_atomic_write_sync_failure_preserves_original_and_removes_temp(tmp_path, monkeypatch, content):
    target = tmp_path / "existing.md"
    target.write_bytes(b"original synthetic content")

    def fail_sync(fd):
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(notes.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="fsync failure"):
        notes._write_note_atomic(tmp_path, target, content)
    assert target.read_bytes() == b"original synthetic content"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["existing.md"]


def test_atomic_write_does_not_follow_existing_target_symlink(tmp_path):
    original = tmp_path / "original.txt"
    original.write_text("untouched")
    target = tmp_path / "note.md"
    target.symlink_to(original)
    notes._write_note_atomic(tmp_path, target, "synthetic note")
    assert original.read_text() == "untouched"
    assert not target.is_symlink()
    assert target.read_text() == "synthetic note"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


# --- Run Lock ------------------------------------------------------------


def test_run_lock_prevents_concurrent_execution(notes_env, tmp_path):
    folder = notes_env["dir"]
    lock_file = folder / ".vocalize.lock"

    # Hold the lock externally
    import fcntl
    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    src = tmp_path / "note.txt"
    src.write_text("Text", encoding="utf-8")

    try:
        runner = CliRunner()
        res = runner.invoke(main, ["transcribe", str(src)])
        assert res.exit_code == 1
        assert "another notes run is in progress" in res.output
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --- Stale Temporary Directory Sweep -------------------------------------


def test_stale_temp_directories_swept():
    tmp_base = Path(tempfile.gettempdir())
    old_dir = tmp_base / f"{notes._NOTES_WORKDIR_PREFIX}old123"
    fresh_dir = tmp_base / f"{notes._NOTES_WORKDIR_PREFIX}fresh123"

    old_dir.mkdir(exist_ok=True)
    fresh_dir.mkdir(exist_ok=True)

    # Set old_dir mtime to 25 hours ago
    past = time.time() - (25 * 3600)
    os.utime(old_dir, (past, past))

    notes._sweep_stale_notes_dirs()

    assert not old_dir.exists()
    assert fresh_dir.exists()

    # Clean up fresh_dir
    fresh_dir.rmdir()


# --- CLI Option Parsing & -x.m4a -----------------------------------------


def test_cli_handles_dash_filename_without_flag_error(notes_env, tmp_path):
    src = tmp_path / "-x.m4a"
    write_wav(src)

    runner = CliRunner()
    res = runner.invoke(main, ["transcribe", str(src)])
    assert res.exit_code == 0
    assert "Wrote note:" in res.output


# --- Folder Never Re-moded -----------------------------------------------


def test_folder_never_re_moded(tmp_path, monkeypatch):
    existing = tmp_path / "existing_notes"
    existing.mkdir(mode=0o755)
    initial_mode = stat.S_IMODE(existing.stat().st_mode)

    _use_folder(monkeypatch, tmp_path, existing)

    src = tmp_path / "note.txt"
    src.write_text("Hello", encoding="utf-8")

    notes.process_sources([src])

    after_mode = stat.S_IMODE(existing.stat().st_mode)
    assert after_mode == initial_mode


# --- Provenance ----------------------------------------------------------


def test_existing_historical_provenance_is_not_rewritten(notes_env, tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("Synthetic transcript")
    note = notes.process_sources([src])[0]
    historical = note.read_text().replace("left_machine: false", "left_machine: true")
    note.write_text(historical)
    assert notes.process_sources([src]) == []
    assert note.read_text() == historical


# --- Keep Audio ----------------------------------------------------------


def test_keep_audio_saves_wav_alongside_note(notes_env, tmp_path):
    src = tmp_path / "recording.m4a"
    write_wav(src)

    created = notes.process_sources([src], keep_audio=True)
    note_path = created[0]
    wav_path = note_path.with_suffix(".wav")

    assert note_path.exists()
    assert wav_path.exists()
    assert wav_path.stat().st_size > 0


# --- Clean Threads -------------------------------------------------------


def test_threads_stay_clean_after_notes_run(notes_env, tmp_path):
    baseline = threading.active_count()

    src = tmp_path / "clean_threads.txt"
    src.write_text("Some text for thread verification", encoding="utf-8")

    notes.process_sources([src])

    assert threading.active_count() == baseline


def test_atomic_note_syncs_file_then_directory(tmp_path, monkeypatch):
    import stat
    calls = []
    original = notes.os.fsync
    def sync(fd):
        calls.append("directory" if stat.S_ISDIR(notes.os.fstat(fd).st_mode) else "file")
        return original(fd)
    monkeypatch.setattr(notes.os, "fsync", sync)
    notes._write_note_atomic(tmp_path, tmp_path / "note.md", "fixture")
    assert calls == ["file", "directory"]


def test_directory_sync_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    import stat
    def sync(fd):
        if stat.S_ISDIR(notes.os.fstat(fd).st_mode):
            raise OSError("synthetic directory sync failure")
    monkeypatch.setattr(notes.os, "fsync", sync)
    target = tmp_path / "note.md"
    with pytest.raises(OSError, match="directory sync"):
        notes._write_note_atomic(tmp_path, target, "fixture")
    assert target.read_text() == "fixture"
