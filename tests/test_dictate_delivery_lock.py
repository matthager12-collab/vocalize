import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

from vocalize import dictate


def _session(tmp_path):
    workdir = Path(tempfile.mkdtemp(prefix="vocalize-dictate-"))
    dictate.CACHE_DIR = tmp_path
    dictate.session_path().write_text(
        json.dumps({"dir": str(workdir), "started": time.time(), "nonce": "n", "state": "transcribing"})
    )
    return workdir


def test_cancel_before_delivery_drops_the_take(tmp_path, monkeypatch):
    workdir = _session(tmp_path)
    monkeypatch.setattr(dictate, "_finish_take", lambda *_: ("words", False))
    monkeypatch.setattr(dictate, "_finish_claim", lambda *_: "live")
    monkeypatch.setattr(dictate, "_stop_file", lambda *_: None)
    monkeypatch.setattr(dictate, "_play", lambda *_: None)
    notices, copied = [], []
    monkeypatch.setattr(dictate, "_notify", notices.append)
    monkeypatch.setattr(dictate, "copy_to_clipboard", copied.append)
    monkeypatch.setattr(dictate, "_write_copied_marker", lambda *_: None)
    assert dictate.cancel({}) == 0
    assert dictate._stop(workdir, None, 0, {"paste": True}) == 0
    assert copied == [] and not dictate.copied_path().exists()
    assert dictate._NOTIFY_COPIED not in notices
    shutil.rmtree(workdir, ignore_errors=True)


def test_cancel_waits_for_delivery_lock(tmp_path, monkeypatch):
    workdir = _session(tmp_path)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(dictate, "_finish_take", lambda *_: ("words", False))
    monkeypatch.setattr(dictate, "_finish_claim", lambda *_: "live")
    monkeypatch.setattr(dictate, "_stop_file", lambda *_: None)
    monkeypatch.setattr(dictate, "_play", lambda *_: None)
    monkeypatch.setattr(dictate, "_notify", lambda *_: None)

    def copy(_text):
        entered.set()
        release.wait(1)

    monkeypatch.setattr(dictate, "copy_to_clipboard", copy)
    stopped = threading.Thread(target=dictate._stop, args=(workdir, None, 0, {}))
    stopped.start()
    assert entered.wait(1)
    cancelled = threading.Thread(target=dictate.cancel, args=({},))
    cancelled.start()
    time.sleep(0.05)
    assert cancelled.is_alive()
    release.set()
    stopped.join(1)
    cancelled.join(1)
    assert not cancelled.is_alive()
    shutil.rmtree(workdir, ignore_errors=True)


def test_cancel_does_not_refuse_when_lock_is_unavailable(tmp_path, monkeypatch):
    _session(tmp_path)
    monkeypatch.setattr(dictate, "_finish_claim", lambda *_: "live")
    monkeypatch.setattr(dictate, "_play", lambda *_: None)
    monkeypatch.setattr(dictate, "_notify", lambda *_: None)
    clock = iter((0, 11))
    monkeypatch.setattr(dictate.time, "monotonic", lambda: next(clock))
    assert dictate.cancel({}) == 0
