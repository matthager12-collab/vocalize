"""Warm reuse never delays recording, loses a take, or warms notes.

Can also be loaded as a pytest plugin to isolate warm paths in older tests:
PYTHONPATH=tests pytest -p test_dictate_warm ...
"""

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
import test_dictate as dictation_fakes
from test_dictate import TRANSCRIPT, lines, stt, write_wav

from vocalize import config, dictate, llm, local
from vocalize.exceptions import DictationError
from vocalize.local import install, llm_manifest, warm, warm_protocol, whisper_manifest

# Reuse the existing executable fakes without changing their test module.
harness = dictation_fakes.harness
loop_pids = dictation_fakes.loop_pids
recorder = dictation_fakes.recorder
transcriber = dictation_fakes.transcriber

@pytest.fixture(autouse=True)
def isolated_warm_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(warm_protocol, "warm_dir", lambda base=None: Path(base) if base else tmp_path / "warm")
    monkeypatch.setattr(llm_manifest, "MODEL_DIR", tmp_path / "llm")


@pytest.fixture
def models(tmp_path, monkeypatch):
    whisper_manifest.MODEL_DIR.mkdir(parents=True)
    whisper_manifest.model_path("small.en").write_bytes(b"model")
    llm_manifest.MODEL_DIR.mkdir()
    (llm_manifest.MODEL_DIR / "model.safetensors").write_bytes(b"model")
    monkeypatch.setattr(dictate, "_uv_or_raise", lambda: "/fake/uv")
    monkeypatch.setattr(local, "uv_path", lambda: "/fake/uv")
    monkeypatch.setattr(install, "installed", lambda *a, **kw: (True, ""))


@pytest.fixture
def warm_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(warm, "ensure_warm", lambda *a, **kw: calls.append(("ensure", a, kw)))
    monkeypatch.setattr(warm, "release", lambda *a, **kw: calls.append(("release", a, kw)))
    monkeypatch.setattr(warm, "request", lambda *a, **kw: None)
    return calls


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


@pytest.fixture
def session(tmp_path, monkeypatch):
    workdir = tmp_path / "take"
    workdir.mkdir()
    assert dictate._claim_session(workdir)
    # _read_session accepts only generated temp-root workdirs in production.
    monkeypatch.setattr(dictate, "_is_workdir", lambda p: p == workdir)
    yield workdir
    dictate._discard(workdir)


@pytest.mark.parametrize("cleanup,kinds", [("off", ["whisper"]), ("local", ["whisper", "llm"])])
def test_start_warms_after_recorder(models, session, harness, monkeypatch, cleanup, kinds):
    order = []
    monkeypatch.setattr(dictate, "_launch_recorder", lambda *a: order.append("recorder") or 123)
    monkeypatch.setattr(dictate, "_cue_the_open_microphone", lambda *a: None)
    monkeypatch.setattr(dictate, "_spawn_self_stop_watcher", lambda *a: None)
    monkeypatch.setattr(warm, "ensure_warm", lambda kind, *a, **kw: order.append(kind))
    assert dictate._start(session, stt(cleanup=cleanup)) == 0
    wait_for(lambda: len(order) == len(kinds) + 1)
    assert order == ["recorder", *kinds]


def test_start_never_waits_for_slow_warming(models, session, harness, monkeypatch):
    """The microphone and the cue never wait on warming (review G2). The only
    wait is the bounded join at the very end, which lets the spawn happen
    before the CLI's sys.exit kills a daemon thread."""
    entered, finished = threading.Event(), threading.Event()
    marks = []

    def slow(*args, **kwargs):
        entered.set()
        time.sleep(5)
        finished.set()

    monkeypatch.setattr(warm, "ensure_warm", slow)
    monkeypatch.setattr(dictate, "_launch_recorder", lambda *a: marks.append("mic") or 123)
    monkeypatch.setattr(dictate, "_cue_the_open_microphone",
                        lambda *a: marks.append(("cue", time.monotonic() - started)))
    monkeypatch.setattr(dictate, "_spawn_self_stop_watcher", lambda *a: None)
    started = time.monotonic()
    try:
        assert dictate._start(session, stt()) == 0
        assert marks[0] == "mic" and marks[1][1] < 0.250  # cued without waiting
        assert time.monotonic() - started < dictate._WARM_JOIN + 0.250
        assert entered.wait(1)
    finally:
        assert finished.wait(6)  # no thread survives fixture teardown


def test_specs_keep_runtime_bounds_offline_env_and_fingerprints(models, tmp_path):
    specs = dictate._warm_specs(stt(cleanup="local", warm_minutes=15), base=tmp_path)
    assert [spec[0] for spec in specs] == ["whisper", "llm"]
    for kind, argv, env, fingerprint in specs:
        assert argv[argv.index("--socket") + 1] == str(tmp_path / f"{kind}.sock")
        assert argv[argv.index("--warm-seconds") + 1] == "900"
        assert argv[argv.index("--abandon-seconds") + 1] == "1860"
        manifest = whisper_manifest if kind == "whisper" else llm_manifest
        model = manifest.model_path("small.en") if kind == "whisper" else manifest.MODEL_DIR / "model.safetensors"
        assert fingerprint == warm_protocol.fingerprint(manifest.worker_path(), manifest.RUNTIME_PACKAGE, model)
        assert "--once" not in argv
        if kind == "llm":
            assert "--offline" in argv
            assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
        else:
            assert env is None
            assert argv[argv.index("--beam-size") + 1] == "5"


def test_missing_or_broken_specs_do_not_suppress_other_model(models, monkeypatch):
    whisper_manifest.model_path("small.en").unlink()
    assert [spec[0] for spec in dictate._warm_specs(stt(cleanup="local"))] == ["llm"]
    monkeypatch.setattr(install, "installed", lambda *a, **kw: (False, "absent"))
    assert dictate._warm_specs(stt(cleanup="local")) == []
    assert dictate._warm_specs(stt(model="../bad")) == []


@pytest.mark.parametrize("reply", [{"ok": True, "text": "Hello.\x00"}, None, {"ok": False}, {"ok": True, "text": 5}])
def test_transcribe_warm_or_exactly_one_fallback(transcriber, harness, tmp_path, monkeypatch, reply):
    transcriber()
    calls = []
    monkeypatch.setattr(warm, "request", lambda *a, **kw: calls.append((a, kw)) or reply)
    wav = write_wav(tmp_path / "clip.wav")
    text = dictate.transcribe(wav, stt(vocabulary=["pyproject"]))
    success = isinstance(reply, dict) and isinstance(reply.get("text"), str)
    assert text == ("Hello." if success else TRANSCRIPT)
    assert lines(harness.uv_argv).count("--transcribe") == (0 if success else 1)
    args, kwargs = calls[0]
    assert args[0] == "whisper"
    assert args[1] == {"op": "transcribe", "wav": str(wav), "language": "en", "initial_prompt": "Vocabulary: pyproject."}
    assert kwargs["deadline_s"] == dictate._transcribe_timeout(0.2)


@pytest.mark.parametrize("outcome", ["success", "silence", "failure", "cancel", "clipboard", "cancel-during-finish", "fail"])
def test_every_exit_releases_original_nonce(session, harness, monkeypatch, warm_calls, outcome):
    nonce = dictate._session_nonce(session)

    def finish(*args):
        if outcome == "failure":
            raise DictationError("failed")
        if outcome == "cancel-during-finish":
            dictate._release_session(session)
        return (None if outcome == "silence" else TRANSCRIPT), False

    monkeypatch.setattr(dictate, "_finish_take", finish)
    if outcome == "clipboard":
        monkeypatch.setattr(dictate, "copy_to_clipboard", lambda *a: (_ for _ in ()).throw(DictationError("failed")))
    if outcome == "cancel":
        code = dictate._cancel(session, None, 0, stt())
    elif outcome == "fail":
        code = dictate._fail(session, stt(), dictate._NOTIFY_FAILED)
    else:
        code = dictate._stop(session, None, 0, stt(paste=True))
    assert code == (1 if outcome in ("failure", "clipboard", "fail") else 0)
    assert [(a[0], a[1]) for op, a, kw in warm_calls if op == "release"] == [("whisper", nonce), ("llm", nonce)]
    if outcome == "cancel-during-finish":
        assert harness.clipboard() == ""


def test_cleanup_uses_warm_but_notes_never_do(models, monkeypatch):
    requests, runs = [], []
    monkeypatch.setattr(warm, "request", lambda *a, **kw: requests.append((a, kw)) or {"ok": True, "text": TRANSCRIPT})
    monkeypatch.setattr(llm, "LOCAL_RUN_SEAM", lambda *a, **kw: runs.append((a, kw)) or subprocess.CompletedProcess(a, 0, json.dumps({"ok": True, "text": TRANSCRIPT})))
    assert llm.cleanup_transcript(TRANSCRIPT, "local") == (TRANSCRIPT, True)
    assert len(requests) == 1 and runs == []
    assert requests[0][0][1]["system"] == llm.CLEANUP_PROMPT.rstrip() + "\n\n" + llm.DATA_BOUNDARY
    assert llm.summarize(TRANSCRIPT, "Summarize", "local") == TRANSCRIPT
    assert len(requests) == 1 and len(runs) == 1
    llm._local("system", TRANSCRIPT, 10, 4096, warm_ok=True)
    assert requests[-1][0][1]["max_tokens"] == 1024


@pytest.mark.parametrize("reply", [None, {"ok": False}, {"ok": True, "text": 7}, RuntimeError("private")])
def test_llm_warm_failures_fall_back_once(models, monkeypatch, reply):
    calls = []

    def request(*a, **kw):
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(warm, "request", request)
    monkeypatch.setattr(llm, "LOCAL_RUN_SEAM", lambda *a, **kw: calls.append(kw) or subprocess.CompletedProcess(a, 0, json.dumps({"ok": True, "text": TRANSCRIPT})))
    assert llm._local("system", TRANSCRIPT, 10, 512, warm_ok=True) == TRANSCRIPT
    assert len(calls) == 1


def test_resume_reuses_nonce(models, session, harness, monkeypatch, warm_calls):
    nonce = dictate._session_nonce(session)
    dictate._write_paused_marker(session, time.time(), 1)
    monkeypatch.setattr(dictate, "_launch_recorder", lambda *a: 123)
    monkeypatch.setattr(dictate, "_wait_for_audio", lambda *a: None)
    monkeypatch.setattr(dictate, "_spawn_self_stop_watcher", lambda *a: None)
    assert dictate.resume(stt()) == 0
    wait_for(lambda: any(op == "ensure" for op, a, kw in warm_calls))
    assert warm_calls[0][1][3] == nonce


@pytest.mark.parametrize("fails", [False, True])
def test_listen_warms_and_releases(models, harness, monkeypatch, warm_calls, fails):
    monkeypatch.setattr(dictate, "_launch_recorder", lambda *a: 123)
    monkeypatch.setattr(dictate, "_cue_the_open_microphone", lambda *a: None)
    monkeypatch.setattr(dictate, "_wait_for_exit", lambda *a: None)

    def finish(*a):
        if fails:
            raise DictationError("failed")
        return TRANSCRIPT, False

    monkeypatch.setattr(dictate, "_finish_take", finish)
    wait = lambda *a: wait_for(lambda: any(op == "ensure" for op, a, kw in warm_calls))
    if fails:
        with pytest.raises(DictationError):
            dictate.listen(stt(), wait=wait)
    else:
        assert dictate.listen(stt(), wait=wait) == TRANSCRIPT
    nonce = next(a[3] for op, a, kw in warm_calls if op == "ensure")
    assert [(a[0], a[1]) for op, a, kw in warm_calls if op == "release"] == [("whisper", nonce), ("llm", nonce)]


def test_release_errors_never_escape_or_skip_llm(monkeypatch):
    calls = []

    def broken(kind, nonce):
        calls.append(kind)
        raise RuntimeError("private")

    monkeypatch.setattr(warm, "release", broken)
    dictate._warm_down("nonce")
    assert calls == ["whisper", "llm"]


def test_missing_nonce_never_warms(tmp_path, warm_calls):
    dictate._warm_up(tmp_path, config.resolve_stt({}))
    assert warm_calls == []


def test_hold_start_is_covered(models, recorder, warm_calls):
    recorder()
    assert dictate.start_hold(stt()) == 0
    try:
        wait_for(lambda: any(op == "ensure" for op, a, kw in warm_calls))
    finally:
        dictate.cancel(stt())


def test_wav_input_uses_warm_without_starting_servers(transcriber, tmp_path, monkeypatch, warm_calls):
    transcriber()
    monkeypatch.setattr(warm, "request", lambda *a, **kw: {"ok": True, "text": TRANSCRIPT})
    assert dictate.transcribe_wav(write_wav(tmp_path / "clip.wav"), stt()) == TRANSCRIPT
    assert warm_calls == []


def test_cancel_during_transcription_releases_immediately(session, harness, monkeypatch, warm_calls):
    nonce = dictate._session_nonce(session)
    monkeypatch.setattr(dictate, "_finish_claim", lambda *a: "live")
    assert dictate.cancel(stt()) == 0
    assert [a for op, a, kw in warm_calls if op == "release"] == [("whisper", nonce), ("llm", nonce)]


def test_cancel_during_warm_up_releases_late_lease(models, session, monkeypatch, warm_calls):
    entered, resume, released = threading.Event(), threading.Event(), threading.Event()
    nonce = dictate._session_nonce(session)
    calls = []

    def slow(kind, *a, **kw):
        calls.append(kind)
        entered.set()
        assert resume.wait(2)

    def release(kind, lease):
        assert (kind, lease) == ("whisper", nonce)
        released.set()

    monkeypatch.setattr(warm, "ensure_warm", slow)
    monkeypatch.setattr(warm, "release", release)
    dictate._warm_up(session, stt(cleanup="local"))
    try:
        assert entered.wait(1)
        dictate._release_session(session)
    finally:
        resume.set()
        assert released.wait(1)
    assert calls == ["whisper"]  # the LLM is never leased for the cancelled take


def test_broken_warm_client_never_breaks_transcription(transcriber, harness, tmp_path, monkeypatch):
    transcriber()

    def broken(*a, **kw):
        raise RuntimeError("private")

    monkeypatch.setattr(warm, "request", broken)
    assert dictate.transcribe(write_wav(tmp_path / "clip.wav"), stt()) == TRANSCRIPT
    assert lines(harness.uv_argv).count("--transcribe") == 1


def test_failed_whisper_warm_up_still_attempts_llm(models, session, monkeypatch):
    calls = []
    done = threading.Event()

    def broken(kind, *a, **kw):
        calls.append(kind)
        if kind == "llm":
            done.set()
        raise RuntimeError("private")

    monkeypatch.setattr(warm, "ensure_warm", broken)
    dictate._warm_up(session, stt(cleanup="local"))
    assert done.wait(1)
    assert calls == ["whisper", "llm"]


def test_failed_thread_start_never_breaks_dictation(models, session, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("private")

    monkeypatch.setattr(dictate.threading, "Thread", broken)
    dictate._warm_up(session, stt())


def test_start_lets_a_quick_warm_up_finish_before_it_returns(models, session, harness, monkeypatch):
    """sys.exit follows `_start` at once; the spawn must already have happened."""
    spawned = []

    def quick(*args, **kwargs):
        time.sleep(0.2)
        spawned.append(args[0])

    monkeypatch.setattr(warm, "ensure_warm", quick)
    monkeypatch.setattr(dictate, "_launch_recorder", lambda *a: 123)
    monkeypatch.setattr(dictate, "_cue_the_open_microphone", lambda *a: None)
    monkeypatch.setattr(dictate, "_spawn_self_stop_watcher", lambda *a: None)
    assert dictate._start(session, stt()) == 0
    assert spawned == ["whisper"]  # done by the time _start returned
