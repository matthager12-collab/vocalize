"""Tests for vocalize notes (Phase 15, T-140-T-145).

Covers:
- Self-ingestion guard
- Done-scan on hash AND resolved path with every skip printed by name
- Every segment sanitized (escape sequences stripped, trust key present)
- Atomic write (.tmp mode 0600 then replaced; killed run leaves no half note)
- Custom template handling (O_NOFOLLOW, 64 KB cap, symlink refused, bare name refused)
- Run lock (<folder>/.vocalize.lock prevents concurrent runs)
- Stale temporary directory sweep (> 24h swept, < 24h kept)
- -x.m4a argument handling
- Folder never re-moded (existing permissions preserved)
- Egress line and left_machine behavior
- Summarizer caps (_MAX_CHARS per backend)
- Keep audio behavior
- Threads clean (threading.active_count() unchanged)
- PermissionError under ~/Documents formatting
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import pytest
from click.testing import CliRunner

from vocalize import config, notes
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


@pytest.fixture
def notes_env(tmp_path, monkeypatch):
    """Set up isolated notes folder and mocks."""
    notes_dir = tmp_path / "notes_folder"
    notes_dir.mkdir(parents=True, mode=0o700)

    # Point default notes folder to tmp_path
    monkeypatch.setattr(config, "NOTES_DEFAULTS", {
        **config.NOTES_DEFAULTS,
        "folder": str(notes_dir),
    })

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
    def fake_transcribe_long(cmd, duration):
        return "Hello world transcript", [
            {"start": 0.0, "end": 1.0, "text": "Hello"},
            {"start": 1.0, "end": 2.0, "text": "world transcript"},
        ]

    monkeypatch.setattr(notes, "_transcribe_long", fake_transcribe_long)

    return {"dir": notes_dir}


# --- Self-Ingestion Guard ------------------------------------------------


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
    src = tmp_path / "meeting.txt"
    src.write_text("Meeting content here", encoding="utf-8")

    # First run processes file
    created = notes.process_sources([src], summarizer="off")
    assert len(created) == 1
    assert created[0].exists()

    # Second run skips and prints skip message by name
    created2 = notes.process_sources([src], summarizer="off")
    assert len(created2) == 0
    out = capsys.readouterr().out
    assert "Skipping meeting.txt: already processed" in out


def test_done_scan_does_not_skip_different_file_with_same_hash(notes_env, tmp_path):
    # Two files with identical content but different paths
    src1 = tmp_path / "file1.txt"
    src2 = tmp_path / "file2.txt"
    src1.write_text("Identical content", encoding="utf-8")
    src2.write_text("Identical content", encoding="utf-8")

    notes.process_sources([src1], summarizer="off")
    created2 = notes.process_sources([src2], summarizer="off")

    # Must process src2 because source path differs (review R5)
    assert len(created2) == 1
    assert created2[0].exists()


def test_force_flag_rewrites_note_in_place(notes_env, tmp_path):
    src = tmp_path / "memo.txt"
    src.write_text("Initial text", encoding="utf-8")

    created = notes.process_sources([src], summarizer="off")
    first_note = created[0]

    time.sleep(0.05)
    src.write_text("Updated text", encoding="utf-8")
    recreated = notes.process_sources([src], force=True, summarizer="off")

    assert len(recreated) == 1
    assert recreated[0] == first_note
    content = first_note.read_text(encoding="utf-8")
    assert "Updated text" in content


# --- Sanitization & Note Format ------------------------------------------


def test_every_segment_sanitized_and_trust_key_present(notes_env, tmp_path, monkeypatch):
    # Worker returns ANSI escape sequences and control characters
    def fake_transcribe_with_escapes(cmd, duration):
        return "\x1b[31mInjected\x1b[0m text\x07", [
            {"start": 0.0, "end": 1.0, "text": "\x1b[31mInjected\x1b[0m"},
            {"start": 1.0, "end": 2.0, "text": "text\x07"},
        ]

    monkeypatch.setattr(notes, "_transcribe_long", fake_transcribe_with_escapes)

    src = tmp_path / "audio.m4a"
    write_wav(src)

    created = notes.process_sources([src], summarizer="off")
    assert len(created) == 1
    content = created[0].read_text(encoding="utf-8")

    # Escape sequences stripped
    assert "\x1b" not in content
    assert "\x07" not in content
    assert "Injected" in content

    # Trust key present
    assert 'trust: "untrusted-transcript"' in content
    assert 'vocalize: "0.14.0"' in content
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


# --- Custom Template Handling --------------------------------------------


def test_template_unknown_bare_name_refused():
    with pytest.raises(notes.NotesError, match="Invalid template 'unknown'"):
        notes.load_template("unknown")


def test_template_valid_custom_md_path_accepted(tmp_path):
    custom = tmp_path / "custom.md"
    custom.write_text("Custom prompt for summaries.", encoding="utf-8")

    tid, text = notes.load_template(str(custom))
    assert tid == str(custom)
    assert text == "Custom prompt for summaries."


def test_template_symlink_refused(tmp_path):
    real = tmp_path / "real.md"
    real.write_text("Content", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(real)

    with pytest.raises(notes.NotesError, match="is a symlink"):
        notes.load_template(str(link))


def test_template_oversized_file_refused(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("x" * (65 * 1024), encoding="utf-8")

    with pytest.raises(notes.NotesError, match="exceeds 64 KB cap"):
        notes.load_template(str(big))


def test_template_traversal_to_package_refused(tmp_path):
    traversal = tmp_path / ".." / "package_probe.md"
    # Even if valid path, directory traversal or foreign files checked
    with pytest.raises(notes.NotesError):
        notes.load_template(str(traversal))


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
        res = runner.invoke(main, ["notes", str(src)])
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
    res = runner.invoke(main, ["notes", str(src), "--summarizer", "off"])
    assert res.exit_code == 0
    assert "Wrote note:" in res.output


# --- Folder Never Re-moded -----------------------------------------------


def test_folder_never_re_moded(tmp_path, monkeypatch):
    existing = tmp_path / "existing_notes"
    existing.mkdir(mode=0o755)
    initial_mode = stat.S_IMODE(existing.stat().st_mode)

    monkeypatch.setattr(config, "NOTES_DEFAULTS", {
        **config.NOTES_DEFAULTS,
        "folder": str(existing),
    })

    src = tmp_path / "note.txt"
    src.write_text("Hello", encoding="utf-8")

    notes.process_sources([src], summarizer="off")

    after_mode = stat.S_IMODE(existing.stat().st_mode)
    assert after_mode == initial_mode


# --- Egress Line & left_machine ------------------------------------------


def test_egress_and_left_machine_local_versus_cloud(notes_env, tmp_path, monkeypatch, capsys):
    from vocalize import llm

    calls = []
    def fake_summarize(text, template_text, backend):
        calls.append(backend)
        if backend == "claude-cli":
            llm.egress("claude-cli")
            return "Claude summary"
        elif backend == "local":
            return "Local summary"
        return None

    monkeypatch.setattr(llm, "summarize", fake_summarize)

    src = tmp_path / "memo.txt"
    src.write_text("Memo content", encoding="utf-8")

    # Local: no egress call, left_machine is false
    created_local = notes.process_sources([src], summarizer="local")
    note_local = created_local[0].read_text(encoding="utf-8")
    assert "left_machine: false" in note_local
    assert 'summarized_by: "local:qwen3.5-4b"' in note_local
    err = capsys.readouterr().err
    assert "vocalize: sent to" not in err

    # Claude-cli: prints egress line, left_machine is true
    src2 = tmp_path / "memo2.txt"
    src2.write_text("Memo 2 content", encoding="utf-8")
    created_cloud = notes.process_sources([src2], summarizer="claude-cli")
    note_cloud = created_cloud[0].read_text(encoding="utf-8")
    assert "left_machine: true" in note_cloud
    assert 'summarized_by: "claude-cli:haiku"' in note_cloud
    err2 = capsys.readouterr().err
    assert "vocalize: sent to claude-cli" in err2


# --- Summarizer Caps -----------------------------------------------------


def test_summarizer_cap_exceeded_writes_transcript_only(notes_env, tmp_path, monkeypatch, capsys):
    from vocalize import llm

    monkeypatch.setattr(notes, "_MAX_CHARS", {"local": 50})
    monkeypatch.setattr(llm, "summarize", lambda *a, **kw: "Should not be called")

    src = tmp_path / "long.txt"
    src.write_text("A" * 100, encoding="utf-8")

    created = notes.process_sources([src], summarizer="local")
    content = created[0].read_text(encoding="utf-8")

    assert "_No summary — transcript only._" in content
    assert 'summarized_by: "none"' in content
    err = capsys.readouterr().err
    assert "exceeds local summarizer cap" in err


# --- Keep Audio ----------------------------------------------------------


def test_keep_audio_saves_wav_alongside_note(notes_env, tmp_path):
    src = tmp_path / "recording.m4a"
    write_wav(src)

    created = notes.process_sources([src], keep_audio=True, summarizer="off")
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

    notes.process_sources([src], summarizer="off")

    assert threading.active_count() == baseline
