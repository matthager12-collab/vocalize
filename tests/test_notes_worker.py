"""Exercise worker framing with synthetic Python children; no models or audio."""

import json
import sys
import time

import pytest

from vocalize import notes


@pytest.fixture
def worker(monkeypatch):
    children = []
    real_popen = notes.subprocess.Popen

    def tracked(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(notes.subprocess, "Popen", tracked)
    monkeypatch.setattr(notes, "_SEGMENT_TIMEOUT", 0.25)

    def run(script, *args):
        return notes._transcribe_long([sys.executable, "-c", script, *args], 3600)

    yield run
    for child in children:
        was_reaped = child.returncode is not None
        if not was_reaped:
            child.kill()
            child.wait()
        assert was_reaped, "worker child was not reaped"
        assert child.stdout.closed


def reply_script(reply, *, newline=True):
    frame = json.dumps(reply) + ("\n" if newline else "")
    return f"import sys; sys.stdout.write({frame!r}); sys.stdout.flush()"


def test_long_worker_receives_only_supplied_runtime_environment(monkeypatch):
    from vocalize.local import runtime

    monkeypatch.setenv("OPENAI_API_KEY", "SECRET_CANARY")
    monkeypatch.setenv("PYTHONPATH", "SECRET_CANARY")
    script = (
        "import json,os; "
        "assert 'OPENAI_API_KEY' not in os.environ and 'PYTHONPATH' not in os.environ; "
        "assert os.environ['HF_HUB_OFFLINE']=='1'; "
        "print(json.dumps({'ok':True,'text':'synthetic'}))"
    )
    assert notes._transcribe_long(
        [sys.executable, "-I", "-c", script], 1, env=runtime.environment(),
    ) == ("synthetic", [])


def test_partial_line_cannot_bypass_timeout(worker):
    start = time.monotonic()
    with pytest.raises(notes.NotesError, match="timed out"):
        worker('import sys,time; sys.stdout.write("{"); sys.stdout.flush(); time.sleep(0.8); print(\'"ok":true,"text":"late"}\')')
    assert time.monotonic() - start < 0.7


def test_stderr_flood_does_not_block_worker(worker, capsys):
    result = worker('import sys; sys.stderr.write("SECRET_CANARY\\nforged: success" * 100000); print(\'{"ok":true,"text":"hello"}\')')
    assert result == ("hello", [])
    assert "SECRET_CANARY" not in str(capsys.readouterr())


@pytest.mark.parametrize("reply", [
    [], {"ok": "yes", "text": "bad"}, {"ok": True, "text": []},
    {"progress": "SECRET_CANARY\nforged"}, {"progress": float("inf")},
    {"progress": -1}, {"progress": True}, {"unexpected": "data"},
    {"progress": 10 ** 400}, {"progress": 999999},
    {"ok": True, "text": "hello", "command": "unexpected"},
    {"ok": True, "segments": {}},
    {"ok": True, "segments": [{"start": 2, "end": 1, "text": "bad"}]},
    {"ok": True, "segments": [{"start": float("nan"), "end": 1, "text": "bad"}]},
    {"ok": True, "segments": [{"start": 0, "end": 1, "text": []}]},
])
def test_malformed_reply_fails_privately(worker, reply, capsys):
    with pytest.raises(notes.NotesError) as exc:
        worker(reply_script(reply))
    assert "SECRET_CANARY" not in str(exc.value) + str(capsys.readouterr())


def test_oversized_frame_rejected(worker, monkeypatch):
    monkeypatch.setattr(notes, "_MAX_WORKER_LINE_BYTES", 1024, raising=False)
    with pytest.raises(notes.NotesError, match="limit"):
        worker(reply_script({"ok": True, "text": "x" * 2000}))


def test_total_output_is_bounded(worker, monkeypatch):
    monkeypatch.setattr(notes, "_MAX_WORKER_OUTPUT_BYTES", 100)
    with pytest.raises(notes.NotesError, match="limit"):
        worker('print(\'{"progress": 1}\\n\' * 100, end="")')


def test_segment_count_is_bounded(worker, monkeypatch):
    monkeypatch.setattr(notes, "_MAX_WORKER_SEGMENTS", 1)
    segment = {"start": 0, "end": 1, "text": "hello"}
    with pytest.raises(notes.NotesError, match="segments"):
        worker(reply_script({"ok": True, "segments": [segment, segment]}))


@pytest.mark.parametrize("script", [
    'import time; print(\'{"ok":true,"text":"hello"}\', flush=True); time.sleep(0.8)',
    'import os,time; os.close(1); time.sleep(0.8)',
    'import time; [(print(\'{"progress":0}\', flush=True), time.sleep(0.05)) for _ in range(16)]',
])
def test_no_exit_or_repeated_progress_cannot_bypass_timeout(worker, script):
    with pytest.raises(notes.NotesError, match="timed out"):
        worker(script)


def test_absolute_deadline_bounds_advancing_progress(worker, monkeypatch):
    monkeypatch.setattr(notes, "_WORKER_DEADLINE", 0.2)
    with pytest.raises(notes.NotesError, match="timed out"):
        worker('import time,json; [(print(json.dumps({"progress": n}), flush=True), time.sleep(0.04)) for n in range(20)]')


def test_regressing_progress_rejected(worker):
    with pytest.raises(notes.NotesError, match="progress"):
        worker('print(\'{"progress": 10}\\n{"progress": 9}\')')


def test_duplicate_final_reply_rejected(worker):
    with pytest.raises(notes.NotesError, match="invalid"):
        worker(reply_script({"ok": True, "text": "hello"}) + "; " + reply_script({"ok": True, "text": "again"}))


def test_failure_does_not_echo_private_diagnostics(worker, capsys):
    with pytest.raises(notes.NotesError) as exc:
        worker(reply_script({"ok": False, "error": "SECRET_CANARY\nforged: success"}))
    assert "SECRET_CANARY" not in str(exc.value) + str(capsys.readouterr())
    assert "forged" not in str(exc.value)


def test_success_reply_with_nonzero_exit_rejected(worker):
    with pytest.raises(notes.NotesError):
        worker(reply_script({"ok": True, "text": "hello"}) + "; sys.exit(3)")


def test_valid_segments_and_partial_final_frame(worker):
    result = worker('print(\'{"progress": 30}\'); ' + reply_script({
        "ok": True, "text": "unused", "segments": [
            {"start": 0, "end": 1, "text": "hello\u0007"},
            {"start": 1, "end": 2, "text": "world"},
        ],
    }, newline=False))
    assert result == ("hello world", [
        {"start": 0, "end": 1, "text": "hello"},
        {"start": 1, "end": 2, "text": "world"},
    ])


def test_shell_metacharacters_are_inert_arguments(worker, tmp_path):
    marker = tmp_path / "should-not-exist"
    argument = f"--option; $(touch {marker})"
    result = worker('import json,sys; print(json.dumps({"ok": True, "text": sys.argv[1]}))', argument)
    assert result == (argument, [])
    assert not marker.exists()


def test_executable_payload_is_not_evaluated(worker, tmp_path):
    marker = tmp_path / "should-not-exist"
    payload = f"__import__('pathlib').Path({str(marker)!r}).touch()"
    with pytest.raises(notes.NotesError):
        worker(f"print({payload!r})")
    assert not marker.exists()


UV_OFFLINE_STDERR = (
    "  hint: Packages were unavailable because the network was disabled.\n"
)


@pytest.mark.parametrize("flood", [0, 200000])
def test_offline_start_failure_says_install_again_privately(worker, capsys, flood):
    script = (
        "import sys; "
        f"sys.stderr.write('SECRET_CANARY ' * {flood}); "
        f"sys.stderr.write('SECRET_CANARY\\n' + {UV_OFFLINE_STDERR!r}); sys.exit(2)"
    )
    with pytest.raises(notes.NotesError) as exc:
        worker(script)
    message = str(exc.value)
    assert "could not start offline" in message
    assert "run `vocalize local install --stt --engine whisper` again" in message
    assert "SECRET_CANARY" not in message + str(capsys.readouterr())
    assert "network was disabled" not in message


def test_other_worker_exit_is_not_called_offline(worker, capsys):
    with pytest.raises(notes.NotesError) as exc:
        worker('import sys; sys.stderr.write("Traceback: SECRET_CANARY"); sys.exit(1)')
    assert str(exc.value) == "Transcription worker failed"
    assert "SECRET_CANARY" not in str(capsys.readouterr())
