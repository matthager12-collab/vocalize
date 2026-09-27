"""Two-type delivery tests; every subprocess is fake."""
import json
import subprocess
from pathlib import Path

import pytest

from vocalize import dictate


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def run(argv, **kwargs):
        recorded.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"ok": true, "change": 1}', '')

    monkeypatch.setattr(dictate.subprocess, 'run', run)
    return recorded


def deliver(monkeypatch, tmp_path, raw, cleaned, backend='local', skipped=False):
    for name in ('_mark_finishing', '_stop_file', '_refresh_claim', '_discard',
                 '_trim_cue', '_join_segments'):
        monkeypatch.setattr(dictate, name, lambda *a: None)
    monkeypatch.setattr(dictate, '_is_silent', lambda *a: False)
    monkeypatch.setattr(dictate, 'transcribe', lambda *a: raw)
    monkeypatch.setattr(dictate.llm, 'cleanup_transcript', lambda *a: (cleaned, not skipped))
    monkeypatch.setattr(dictate, '_session_owns', lambda *a: True)
    monkeypatch.setattr(dictate, '_play', lambda *a: None)
    monkeypatch.setattr(dictate, '_notify', lambda *a: None)
    return dictate._stop(tmp_path, None, 0, {'cleanup': backend})


def test_delivery_uses_json_stdin_and_fixed_argv(monkeypatch, tmp_path, calls):
    raw, cleaned = 'um raw\nwords\x00', 'Raw\nwords\x1b'
    assert deliver(monkeypatch, tmp_path, raw, cleaned) == 0
    argv, kwargs = calls[0]
    assert argv == ['/usr/bin/osascript', '-l', 'JavaScript',
                    str(Path(dictate.__file__).resolve().parent / 'assets' / 'clipboard.js')]
    assert all(raw not in arg and cleaned not in arg for arg in argv)
    assert json.loads(kwargs['input']) == {
        'op': 'write', 'plain': 'Raw words', 'said': 'um raw words',
    }
    assert kwargs == {'input': kwargs['input'], 'capture_output': True, 'text': True,
                      'timeout': dictate._PBCOPY_TIMEOUT, 'check': False}


@pytest.mark.parametrize('backend,skipped', [('local', False), ('off', False), ('local', True)])
def test_unchanged_or_skipped_take_uses_pbcopy(monkeypatch, tmp_path, calls, backend, skipped):
    assert deliver(monkeypatch, tmp_path, 'words', 'words', backend, skipped) == 0
    assert calls[0][0] == [dictate._PBCOPY]
    assert calls[0][1]['input'] == 'words'
    assert len(calls) == 1


@pytest.mark.parametrize('reply', [None, {'ok': False}, {'ok': 'yes'}])
def test_failed_two_type_write_falls_back(monkeypatch, tmp_path, calls, reply):
    monkeypatch.setattr(dictate, '_clipboard', lambda request: reply)
    assert deliver(monkeypatch, tmp_path, 'um words', 'Words') == 0
    assert calls[0][0] == [dictate._PBCOPY]
    assert calls[0][1]['input'] == 'Words'


@pytest.mark.parametrize('stdout,code', [('', 0), ('[]', 0), ('null', 0),
    ('{"ok": true}', 1), ('x' * (1024 * 1024 + 1), 0), ('{"ok":true}', 0)])
def test_reply_validation(monkeypatch, stdout, code):
    monkeypatch.setattr(dictate.subprocess, 'run',
                        lambda *a, **k: subprocess.CompletedProcess(a, code, stdout, ''))
    assert dictate._clipboard({'op': 'read'}) == ({'ok': True} if stdout == '{"ok":true}' else None)


@pytest.mark.parametrize('error', [OSError(), subprocess.TimeoutExpired('osascript', 10),
                                  UnicodeError()])
def test_subprocess_failure_is_private(monkeypatch, error, capsys):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(dictate.subprocess, 'run', fail)
    assert dictate._clipboard({'op': 'read'}) is None
    assert capsys.readouterr() == ('', '')


def test_fixed_script_does_not_construct_source_from_text():
    """Static source check only; this does not execute JXA or touch a clipboard."""
    source = (Path(dictate.__file__).resolve().parent / 'assets' / 'clipboard.js').read_text()
    assert 'eval' not in source
    assert 'Function(' not in source
    assert '+' not in source  # no concatenation of input into script source
    assert 'fileHandleWithStandardInput.readDataToEndOfFile' in source
    assert 'request.expect' in source
    assert source.count('board.changeCount') >= 4


def test_cancelled_take_never_writes(monkeypatch, tmp_path, calls):
    from contextlib import contextmanager
    @contextmanager
    def cancelled():
        monkeypatch.setattr(dictate, '_session_owns', lambda *a: False)
        yield
    monkeypatch.setattr(dictate, '_delivery_lock', cancelled)
    assert deliver(monkeypatch, tmp_path, 'um words', 'Words') == 0
    assert calls == []


def test_skipped_cleanup_cannot_create_an_undo_pair(monkeypatch, tmp_path, calls):
    # A skipped cleanup can strip the spoken verbatim keyword; it still uses pbcopy.
    assert deliver(monkeypatch, tmp_path, 'verbatim words', 'words', skipped=True) == 0
    assert len(calls) == 1
    assert calls[0][0] == [dictate._PBCOPY]
    assert calls[0][1]['input'] == 'words'
