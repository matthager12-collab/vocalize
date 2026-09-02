"""Tests for vocalize.audio — player dispatch, save, and error wrapping.

subprocess.run and the platform/shutil probes are monkeypatched so these
tests don't depend on what's actually installed on the machine running
them.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

import vocalize.audio as audio_module
from vocalize.audio import play, save, stop_playback
from vocalize.exceptions import AudioPlaybackError, NoAudioPlayerError, VocalizeError


class _FakeProc:
    def __init__(self, pid=4242, returncode=0, on_wait=None):
        self.pid = pid
        self._returncode = returncode
        self._on_wait = on_wait

    def wait(self):
        if self._on_wait is not None:
            self._on_wait()
        return self._returncode


def _patch_player(monkeypatch, tmp_path, returncode=0, on_wait=None):
    """Route playback through a fake Popen and the PID file into tmp_path."""
    monkeypatch.setattr(audio_module, "_PID_FILE", tmp_path / "play.pid")
    monkeypatch.setattr(audio_module, "_proc_start_time", lambda pid: "FAKE-START")
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return _FakeProc(returncode=returncode, on_wait=on_wait)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


def test_play_uses_first_available_player(monkeypatch, tmp_path):
    path = tmp_path / "out.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/ffplay" if exe == "ffplay" else None)
    calls = _patch_player(monkeypatch, tmp_path)

    play(path)

    assert calls == [["ffplay", "-nodisp", "-autoexit", str(path)]]


def test_play_raises_when_no_player_found(monkeypatch, tmp_path):
    path = tmp_path / "out.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda exe: None)

    with pytest.raises(NoAudioPlayerError) as excinfo:
        play(path)

    assert str(path) in str(excinfo.value)


def test_play_wraps_player_failure(monkeypatch, tmp_path):
    path = tmp_path / "out.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    _patch_player(monkeypatch, tmp_path, returncode=1)

    with pytest.raises(AudioPlaybackError) as excinfo:
        play(path)

    assert isinstance(excinfo.value, VocalizeError)
    assert "afplay" in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_play_treats_sigterm_exit_as_a_clean_stop(monkeypatch, tmp_path):
    # A negative-SIGTERM exit is `vocalize stop` doing its job — the
    # interrupted speak command must finish quietly, not error out.
    path = tmp_path / "out.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    _patch_player(monkeypatch, tmp_path, returncode=-signal.SIGTERM)

    play(path)  # must not raise


def test_play_records_pid_during_playback_and_clears_it_after(monkeypatch, tmp_path):
    path = tmp_path / "out.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    seen = {}

    def snapshot_pid_file():
        pid_file = tmp_path / "play.pid"
        seen["exists"] = pid_file.is_file()
        seen["content"] = pid_file.read_text() if seen["exists"] else None

    _patch_player(monkeypatch, tmp_path, on_wait=snapshot_pid_file)

    play(path)

    assert seen == {"exists": True, "content": "4242\nFAKE-START\n"}
    assert not (tmp_path / "play.pid").is_file()


def test_finished_play_never_deletes_an_overlapping_plays_record(monkeypatch, tmp_path):
    # A second play overwrote the file while this one was still going; this
    # play's exit must leave that newer, still-live record alone.
    path = tmp_path / "out.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)

    def overwrite_with_other_play():
        (tmp_path / "play.pid").write_text("9999\nOTHER-START\n")

    _patch_player(monkeypatch, tmp_path, on_wait=overwrite_with_other_play)

    play(path)

    assert (tmp_path / "play.pid").read_text() == "9999\nOTHER-START\n"


def test_pid_file_is_cleared_even_when_the_player_fails(monkeypatch, tmp_path):
    path = tmp_path / "out.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    _patch_player(monkeypatch, tmp_path, returncode=1)

    with pytest.raises(AudioPlaybackError):
        play(path)

    assert not (tmp_path / "play.pid").is_file()


def test_windows_command_escapes_quotes_in_path(monkeypatch, tmp_path):
    path = tmp_path / "it's.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    calls = _patch_player(monkeypatch, tmp_path)

    play(path)

    script = calls[0][2]
    assert "it''s.mp3" in script
    # the raw, un-doubled quote sequence would break out of the PowerShell
    # string literal — it must not appear.
    assert "it's.mp3'" not in script


# --- stop_playback -----------------------------------------------------------


def test_stop_with_no_pid_file_reports_nothing_playing(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_module, "_PID_FILE", tmp_path / "play.pid")
    assert stop_playback() is False


def test_stop_with_garbage_pid_file_reports_nothing_playing(monkeypatch, tmp_path):
    pid_file = tmp_path / "play.pid"
    pid_file.write_text("not-a-pid")
    monkeypatch.setattr(audio_module, "_PID_FILE", pid_file)
    assert stop_playback() is False


def _seed_record(monkeypatch, tmp_path, content="4242\nFAKE-START\n",
                 start="FAKE-START", known=True):
    pid_file = tmp_path / "play.pid"
    pid_file.write_text(content)
    monkeypatch.setattr(audio_module, "_PID_FILE", pid_file)
    monkeypatch.setattr(audio_module, "_proc_start_time", lambda pid: start)
    monkeypatch.setattr(audio_module, "_is_known_player", lambda pid: known)
    return pid_file


def test_stop_kills_a_verified_player_and_clears_the_file(monkeypatch, tmp_path):
    pid_file = _seed_record(monkeypatch, tmp_path)
    killed = {}
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.update(pid=pid, sig=sig))

    assert stop_playback() is True
    assert killed == {"pid": 4242, "sig": signal.SIGTERM}
    assert not pid_file.is_file()


def _forbidden_kill(pid, sig):
    raise AssertionError("killed a PID whose identity did not fully match")


def test_stop_never_kills_a_pid_reused_by_a_non_player(monkeypatch, tmp_path):
    # The player died without cleanup and the OS reused its PID — the
    # recorded number now belongs to some innocent process.
    pid_file = _seed_record(monkeypatch, tmp_path, known=False)
    monkeypatch.setattr(os, "kill", _forbidden_kill)

    assert stop_playback() is False
    assert not pid_file.is_file()  # stale record cleaned up


def test_stop_never_kills_a_pid_reused_by_another_player(monkeypatch, tmp_path):
    # Worst case: the recycled PID now belongs to a REAL afplay the user
    # started themselves. The launch-timestamp mismatch is what saves it.
    pid_file = _seed_record(monkeypatch, tmp_path, start="DIFFERENT-START", known=True)
    monkeypatch.setattr(os, "kill", _forbidden_kill)

    assert stop_playback() is False
    assert not pid_file.is_file()


def test_stop_treats_a_record_without_a_timestamp_as_unverifiable(monkeypatch, tmp_path):
    pid_file = _seed_record(monkeypatch, tmp_path, content="4242\n")
    monkeypatch.setattr(os, "kill", _forbidden_kill)

    assert stop_playback() is False
    assert not pid_file.is_file()


def test_stop_handles_a_player_that_just_exited(monkeypatch, tmp_path):
    pid_file = _seed_record(monkeypatch, tmp_path)

    def gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", gone)

    assert stop_playback() is False
    assert not pid_file.is_file()


def test_is_known_player_checks_the_process_name(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="/usr/bin/afplay\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert audio_module._is_known_player(4242) is True

    def fake_run_other(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="python3\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_other)
    assert audio_module._is_known_player(4242) is False

    def fake_run_empty(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_empty)
    assert audio_module._is_known_player(4242) is False


def test_save_creates_parent_directories(tmp_path):
    dest = tmp_path / "a" / "b" / "c.mp3"

    result = save(b"x", dest)

    assert result == dest
    assert dest.read_bytes() == b"x"


def test_save_wraps_permission_error(monkeypatch, tmp_path):
    def raise_permission_error(self, data):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "write_bytes", raise_permission_error)

    with pytest.raises(AudioPlaybackError):
        save(b"x", tmp_path / "out.mp3")


# --- play_sequence -----------------------------------------------------------


def test_play_sequence_plays_every_piece_in_order(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    calls = _patch_player(monkeypatch, tmp_path)
    pieces = [tmp_path / "1.wav", tmp_path / "2.wav", tmp_path / "3.wav"]

    assert audio_module.play_sequence(pieces) is True
    assert calls == [["afplay", str(p)] for p in pieces]


def test_play_sequence_stops_at_the_piece_the_user_stopped(monkeypatch, tmp_path):
    # `vocalize stop` kills the piece that is playing; the rest of the
    # document must not carry on without it.
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    calls = _patch_player(monkeypatch, tmp_path, returncode=-signal.SIGTERM)
    pieces = [tmp_path / "1.wav", tmp_path / "2.wav", tmp_path / "3.wav"]

    assert audio_module.play_sequence(pieces) is False
    assert len(calls) == 1


def test_play_sequence_asks_stop_check_before_each_piece(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    calls = _patch_player(monkeypatch, tmp_path)
    answers = [False, True]

    result = audio_module.play_sequence(
        [tmp_path / "1.wav", tmp_path / "2.wav"], stop_check=lambda: answers.pop(0)
    )

    assert result is False
    assert len(calls) == 1  # the second piece was never started


# --- stitching -----------------------------------------------------------


def _wav(frames: int, *, channels=1, width=2, rate=24000) -> bytes:
    import io
    import wave

    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(b"\x00" * width * channels * frames)
    return out.getvalue()


def _params(data: bytes):
    import io
    import wave

    with wave.open(io.BytesIO(data), "rb") as reader:
        return reader.getparams()


def test_stitch_wav_appends_frames_and_keeps_the_params(tmp_path):
    joined = audio_module.stitch_wav([_wav(100), _wav(250), _wav(7)])

    assert _params(joined).nframes == 357
    assert _params(joined)[:3] == _params(_wav(100))[:3]
    # One header, not three: raw concatenation would be longer than this.
    assert len(joined) < len(_wav(100)) + len(_wav(250)) + len(_wav(7))


def test_stitch_wav_refuses_mismatched_pieces():
    with pytest.raises(AudioPlaybackError, match="different"):
        audio_module.stitch_wav([_wav(10), _wav(10, rate=48000)])


def test_stitch_wav_refuses_something_that_is_not_a_wav():
    with pytest.raises(AudioPlaybackError, match="Could not join"):
        audio_module.stitch_wav([_wav(10), b"not audio at all"])


def test_join_audio_concatenates_mp3_frames():
    assert audio_module.join_audio([b"aaa", b"bbb"], "mp3") == b"aaabbb"


def test_join_audio_stitches_wav():
    joined = audio_module.join_audio([_wav(10), _wav(20)], "wav")

    assert _params(joined).nframes == 30


def test_join_audio_refuses_to_stitch_two_m4a_pieces():
    with pytest.raises(AudioPlaybackError, match="cannot be chunked"):
        audio_module.join_audio([b"piece one", b"piece two"], "m4a")


def test_join_audio_passes_a_single_m4a_piece_straight_through():
    assert audio_module.join_audio([b"the only piece"], "m4a") == b"the only piece"


def test_play_waits_for_the_playback_slot(monkeypatch, tmp_path):
    """A play() started while another read holds the slot must not launch
    its player until the slot frees — the bug this guards against was two
    Claude Code sessions speaking over each other.
    """
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    calls = _patch_player(monkeypatch, tmp_path)
    done = threading.Event()

    def queued_play():
        play(tmp_path / "queued.mp3")
        done.set()

    with audio_module._playback_slot():  # someone else is mid-read
        thread = threading.Thread(target=queued_play)
        thread.start()
        assert not done.wait(0.3)  # still queued behind the held slot
        assert calls == []
    thread.join(timeout=5)
    assert done.is_set()
    assert len(calls) == 1


def test_play_sequence_holds_one_slot_for_the_whole_read(monkeypatch, tmp_path):
    """The slot is acquired once for a whole sequence, not per piece —
    otherwise a queued read could interleave between two chunks.
    """
    acquisitions = []

    @contextmanager
    def counting_slot():
        acquisitions.append(1)
        yield

    monkeypatch.setattr(audio_module, "_playback_slot", counting_slot)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    calls = _patch_player(monkeypatch, tmp_path)

    paths = [tmp_path / f"{i}.mp3" for i in range(3)]
    assert audio_module.play_sequence(paths) is True

    assert acquisitions == [1]
    assert len(calls) == 3


def test_playback_slot_is_a_noop_without_fcntl(monkeypatch):
    """Windows has no fcntl; the slot degrades to a pass-through rather
    than crashing, and leaves no lock file behind.
    """
    monkeypatch.setattr(audio_module, "fcntl", None)
    with audio_module._playback_slot():
        pass
    assert not audio_module._LOCK_FILE.exists()
