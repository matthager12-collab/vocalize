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
import time
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


# --- the interrupt marker and last_stop (DEC-003) ------------------------


def _marker(*, pid=4242, age=0.0) -> Path:
    """A stop marker as stop_playback(remember=True) would have left it."""
    path = audio_module._INTERRUPT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n{time.time() - age}\n", encoding="utf-8")
    return path


def _claim(*, remembered=True, age=0.0) -> Path:
    """The silence order every stop leaves behind (DEC-013)."""
    path = audio_module._STOP_CLAIM_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{time.time() - age}\n{int(remembered)}\n", encoding="utf-8")
    return path


def test_a_remembered_stop_writes_the_interrupt_marker_before_it_signals(monkeypatch, tmp_path):
    # Before, not after: the player can exit — and go looking for this
    # marker — in the microseconds after the signal lands.
    _seed_record(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(
        os, "kill",
        lambda pid, sig: seen.update(marker=audio_module._INTERRUPT_FILE.read_text()),
    )

    assert stop_playback(remember=True) is True

    assert seen["marker"].splitlines()[0] == "4242"


def test_a_plain_stop_leaves_no_interrupt_marker(monkeypatch, tmp_path):
    _seed_record(monkeypatch, tmp_path)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    assert stop_playback() is True

    assert not audio_module._INTERRUPT_FILE.exists()


def test_the_interrupt_marker_is_private(monkeypatch, tmp_path):
    _seed_record(monkeypatch, tmp_path)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    stop_playback(remember=True)

    mode = audio_module._INTERRUPT_FILE.stat().st_mode & 0o777
    assert mode == 0o600


def test_an_interrupt_marker_is_never_written_through_a_symlink(monkeypatch, tmp_path):
    # The path is guessable, so anything running as the user can plant a
    # symlink there and have this truncate whatever it points at.
    target = tmp_path / "someone-elses-file"
    target.write_text("keep me")
    audio_module._INTERRUPT_FILE.parent.mkdir(parents=True, exist_ok=True)
    audio_module._INTERRUPT_FILE.symlink_to(target)
    _seed_record(monkeypatch, tmp_path)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    assert stop_playback(remember=True) is True

    assert target.read_text() == "keep me"


def test_an_interrupt_marker_is_dropped_when_the_stop_hit_nothing(monkeypatch, tmp_path):
    # The player was already gone. Leaving the marker would let the *next*
    # read believe it had been asked to remember itself.
    _seed_record(monkeypatch, tmp_path)

    def gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", gone)

    assert stop_playback(remember=True) is False
    assert not audio_module._INTERRUPT_FILE.exists()


def _stopped_play(monkeypatch, tmp_path, *, on_wait=None):
    """Play one file through a player that exits by SIGTERM."""
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    _patch_player(monkeypatch, tmp_path, returncode=-signal.SIGTERM, on_wait=on_wait)
    path = tmp_path / "3.wav"
    path.write_bytes(b"piece three")
    return play(path), path


def test_a_stopped_player_remembers_the_file_and_the_interrupt_request(monkeypatch, tmp_path):
    returncode, path = _stopped_play(
        monkeypatch, tmp_path, on_wait=lambda: (_marker(), time.sleep(0.05)),
    )

    assert returncode == -signal.SIGTERM
    stop = audio_module.last_stop()
    assert stop.path == path
    assert stop.remembered is True
    assert 0.05 <= stop.elapsed_seconds < 5
    # Consumed by the thread that ran the player, not left for the next one.
    assert not audio_module._INTERRUPT_FILE.exists()


def test_a_stopped_player_without_an_interrupt_request_is_not_remembered(monkeypatch, tmp_path):
    # `vocalize stop` on a plain read: stopped, but nothing to resume.
    _stopped_play(monkeypatch, tmp_path)

    stop = audio_module.last_stop()
    assert stop.path is not None
    assert stop.remembered is False


def test_a_fresh_interrupt_marker_naming_another_player_is_left_alone(monkeypatch, tmp_path):
    _stopped_play(monkeypatch, tmp_path, on_wait=lambda: _marker(pid=9999))

    assert audio_module.last_stop().remembered is False
    assert audio_module._INTERRUPT_FILE.read_text().splitlines()[0] == "9999"


def test_an_interrupt_marker_older_than_the_window_is_removed_but_never_used(monkeypatch, tmp_path):
    _stopped_play(
        monkeypatch, tmp_path,
        on_wait=lambda: _marker(age=audio_module.INTERRUPT_WINDOW + 1),
    )

    assert audio_module.last_stop().remembered is False
    assert not audio_module._INTERRUPT_FILE.exists()


def test_a_malformed_interrupt_marker_is_never_acted_on(monkeypatch, tmp_path):
    def garbage():
        audio_module._INTERRUPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        audio_module._INTERRUPT_FILE.write_text("../../etc/passwd\n")

    _stopped_play(monkeypatch, tmp_path, on_wait=garbage)

    assert audio_module.last_stop().remembered is False
    assert audio_module._INTERRUPT_FILE.exists()  # not ours to delete


def test_a_player_that_finished_consumes_no_interrupt_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)
    _patch_player(monkeypatch, tmp_path, on_wait=_marker)

    play(tmp_path / "whole.mp3")

    assert audio_module.last_stop() == audio_module.LastStop()
    # A read that ended on its own consumes nothing: the marker belongs to
    # whichever player the stopper actually named.
    assert audio_module._INTERRUPT_FILE.exists()


def test_an_interrupt_marker_is_never_read_through_a_symlink(monkeypatch, tmp_path):
    # The mirror of the write side. A symlink planted at the guessable path
    # could otherwise fabricate a stop for a read nobody interrupted — any
    # file whose first two lines read as a PID and a timestamp will do.
    target = tmp_path / "someone-elses-file"
    target.write_text(f"4242\n{time.time()}\n")
    audio_module._INTERRUPT_FILE.parent.mkdir(parents=True, exist_ok=True)
    audio_module._INTERRUPT_FILE.symlink_to(target)

    assert audio_module.take_interrupt_request(4242) is False

    assert target.exists()  # and it was not consumed either


def test_a_stale_interrupt_marker_naming_another_player_is_swept(tmp_path):
    # Nobody is coming for it: the player it names was SIGKILLed before it
    # could look. Leaving it on disk for ever is the deviation T-46's
    # acceptance criterion rules out.
    _marker(pid=9999, age=audio_module.INTERRUPT_WINDOW + 1)

    assert audio_module.take_interrupt_request(4242) is False

    assert not audio_module._INTERRUPT_FILE.exists()


def test_a_remembered_stop_with_nothing_playing_still_leaves_a_marker(monkeypatch, tmp_path):
    # The gap between two streamed pieces: the read is alive, no player is,
    # and the stop has no process to name. Without this marker the next
    # piece plays straight into the open microphone.
    monkeypatch.setattr(audio_module, "_PID_FILE", tmp_path / "play.pid")

    assert stop_playback(remember=True) is False

    assert audio_module._INTERRUPT_FILE.read_text().splitlines()[0] == "0"


def test_an_interrupt_in_the_gap_is_taken_by_the_piece_about_to_play(tmp_path):
    piece = tmp_path / "4.wav"
    _marker(pid=0)
    _claim()

    assert audio_module.take_gap_stop(piece, since=time.time() - 60) is True

    stop = audio_module.last_stop()
    assert (stop.path, stop.elapsed_seconds, stop.remembered) == (piece, 0.0, True)
    assert not audio_module._INTERRUPT_FILE.exists()


def test_an_interrupt_from_before_the_read_is_never_taken_in_the_gap(tmp_path):
    # A dictation that stopped nothing at all leaves this behind. A read
    # started afterwards must not take it as its own and stop itself.
    _marker(pid=0)
    _claim()

    assert audio_module.take_gap_stop(tmp_path / "1.wav", since=time.time() + 1) is False

    assert audio_module.last_stop() == audio_module.LastStop()
    assert audio_module._INTERRUPT_FILE.exists()  # still fresh, still someone's


def test_a_gap_interrupt_marker_past_the_window_is_swept_by_the_next_read(tmp_path):
    _marker(pid=0, age=audio_module.INTERRUPT_WINDOW + 1)
    _claim(age=audio_module.INTERRUPT_WINDOW + 1)

    assert audio_module.take_gap_stop(tmp_path / "1.wav", since=time.time()) is False

    assert not audio_module._INTERRUPT_FILE.exists()


def test_an_unclaimed_interrupt_marker_says_the_stop_found_no_player(tmp_path):
    started = time.time() - 5
    _marker(pid=0)

    assert audio_module.stop_found_no_player(started) is True

    # Consumed by a read, or naming a player that took it: something was
    # stopped, and its record may still be seconds away.
    audio_module._INTERRUPT_FILE.unlink()
    assert audio_module.stop_found_no_player(started) is False
    _marker(pid=4242)
    assert audio_module.stop_found_no_player(started) is False


# --- a stop reaches every read in flight (DEC-013) --------------------


def test_a_plain_stop_in_the_gap_still_stops_the_read(tmp_path):
    """`vocalize stop` between two streamed pieces must stop the read.

    Only a `remember=True` stop ever wrote a marker, so a plain stop in
    the gap was ignored entirely and the queued piece played on.
    """
    _claim(remembered=False)

    assert audio_module.take_gap_stop(tmp_path / "2.wav", since=time.time() - 60) is True

    # Stopped, but nothing to resume: a plain stop records no read.
    assert audio_module.last_stop().remembered is False


def test_a_stop_reaches_the_read_queued_behind_the_one_it_killed(tmp_path):
    """Read A's player is killed; read B is next on the playback lock.

    B starts the instant that lock frees — into the microphone the stop
    was opening. The marker names A's player, so B takes no record, but it
    must still stop.
    """
    _marker(pid=4242)  # the record baton belongs to read A's player
    _claim()

    assert audio_module.take_gap_stop(tmp_path / "1.wav", since=time.time() - 60) is True

    assert audio_module.last_stop().remembered is False  # A's record, not B's
    assert audio_module._INTERRUPT_FILE.exists()  # left for A's player thread


def test_a_read_started_after_the_stop_silences_itself_on_nothing(tmp_path):
    _claim()

    assert audio_module.take_gap_stop(tmp_path / "1.wav", since=time.time() + 1) is False

    assert audio_module.last_stop() == audio_module.LastStop()


def test_a_stop_claim_past_the_window_stops_nothing(tmp_path):
    _claim(age=audio_module.INTERRUPT_WINDOW + 1)

    assert audio_module.take_gap_stop(tmp_path / "1.wav", since=0.0) is False


def test_every_stop_leaves_the_silence_order_behind(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_module, "_PID_FILE", tmp_path / "play.pid")

    assert stop_playback() is False  # nothing was playing

    claim = audio_module._read_stop_claim()
    assert claim is not None
    assert claim[1] is False  # a plain stop still records no read
