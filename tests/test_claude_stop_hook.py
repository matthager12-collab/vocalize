import io
import json
import os
import subprocess

import claude_stop_hook as hook
from click.testing import CliRunner

from vocalize.cli import main as vocalize_cli
from vocalize.exceptions import MissingAPIKeyError


def _assistant(content) -> str:
    return json.dumps({"type": "assistant", "message": {"content": content}})


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _write_transcript(tmp_path, lines) -> str:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _patch_main(monkeypatch, payload, which="/usr/local/bin/vocalize", run=None, stdin=None):
    """Wire up stdin, PATH lookup and subprocess so main() can't shell out."""
    monkeypatch.delenv("VOCALIZE_MAX_CHARS", raising=False)
    monkeypatch.delenv("VOCALIZE_BIN", raising=False)
    monkeypatch.setattr(hook.sys, "argv", ["claude_stop_hook.py"])
    monkeypatch.setattr(
        hook.sys, "stdin", io.StringIO(json.dumps(payload)) if stdin is None else stdin
    )
    monkeypatch.setattr(hook.shutil, "which", lambda name: which)

    calls = []

    class _FakeProc:
        pid = 4242

        def __init__(self, argv):
            self._argv = argv

        def wait(self, timeout=None):
            # Pin the timeout: a Stop hook that can hang forever blocks the
            # session, so this is behaviour, not an implementation detail. It
            # scales with the text being spoken (argv[-1], after the "--").
            assert timeout == hook._speech_timeout(self._argv[-1])
            if run is not None:
                return run(self._argv).returncode
            return 0

    def fake_popen(argv, **kwargs):
        # The new session is behaviour too: it is what makes overflow "ask"
        # degrade (no controlling tty) and what the timeout path kills.
        assert kwargs.get("start_new_session") is True
        calls.append(argv)
        return _FakeProc(argv)

    monkeypatch.setattr(hook.subprocess, "Popen", fake_popen)
    return calls


def test_returns_most_recent_assistant_text(tmp_path):
    path = _write_transcript(
        tmp_path,
        [_assistant([_text("older answer")]), _assistant([_text("newest answer")])],
    )

    assert hook._extract_last_assistant_text(path) == "newest answer"


def test_joins_multiple_text_blocks_in_one_entry(tmp_path):
    path = _write_transcript(
        tmp_path, [_assistant([_text("first part"), _text("second part")])]
    )

    assert hook._extract_last_assistant_text(path) == "first part\nsecond part"


def test_handles_string_shaped_content(tmp_path):
    path = _write_transcript(
        tmp_path, [_assistant([_text("older answer")]), _assistant("plain string answer")]
    )

    assert hook._extract_last_assistant_text(path) == "plain string answer"


def test_falls_back_past_tool_use_only_entry(tmp_path):
    # Walking back to the previous assistant turn is intended: a turn that
    # only ran tools has nothing worth speaking, so the last thing Claude
    # actually said is the right thing to read aloud.
    path = _write_transcript(
        tmp_path,
        [
            _assistant([_text("the spoken answer")]),
            _assistant([{"type": "tool_use", "name": "Read", "input": {}}]),
        ],
    )

    assert hook._extract_last_assistant_text(path) == "the spoken answer"


def test_ignores_malformed_json_lines(tmp_path):
    # The garbage line must sit AFTER the newest assistant entry. The scan
    # walks reversed(lines) and breaks on the first text it finds, so a
    # malformed line placed earlier is never parsed and the JSONDecodeError
    # guard never runs. Trailing is also where it happens for real: a
    # partially-flushed final line while Claude Code is still writing.
    path = _write_transcript(
        tmp_path,
        [
            _assistant([_text("older answer")]),
            _assistant([_text("newest answer")]),
            '{"type": "assistant", "message": {"content": [{"type": "te',
        ],
    )

    assert hook._extract_last_assistant_text(path) == "newest answer"


def test_missing_transcript_returns_empty_string(tmp_path):
    assert hook._extract_last_assistant_text(str(tmp_path / "nope.jsonl")) == ""


def test_text_block_without_text_key_is_ignored(tmp_path):
    path = _write_transcript(
        tmp_path, [_assistant([{"type": "text"}, _text("the good block")])]
    )

    assert hook._extract_last_assistant_text(path) == "the good block"


def test_main_no_ops_without_transcript_path(monkeypatch):
    calls = _patch_main(monkeypatch, {})

    assert hook.main() == 0
    assert calls == []


def test_main_passes_default_max_chars(monkeypatch, tmp_path):
    path = _write_transcript(tmp_path, [_assistant([_text("hello there")])])
    calls = _patch_main(monkeypatch, {"transcript_path": path})

    assert hook.main() == 0
    assert calls == [
        [
            "/usr/local/bin/vocalize",
            "speak",
            "--default-max-chars",
            "500",
            "--play",
            "--",
            "hello there",
        ]
    ]


def test_env_max_chars_is_left_for_the_cli_to_resolve(monkeypatch, tmp_path):
    # The hook must not translate VOCALIZE_MAX_CHARS into --max-chars: the
    # CLI reads the (inherited) environment itself, and a --max-chars flag
    # here would wrongly outrank the user's config-file precedence.
    path = _write_transcript(tmp_path, [_assistant([_text("hello there")])])
    calls = _patch_main(monkeypatch, {"transcript_path": path})
    monkeypatch.setenv("VOCALIZE_MAX_CHARS", "120")

    assert hook.main() == 0
    assert len(calls) == 1
    assert "--max-chars" not in calls[0]
    assert "120" not in calls[0]
    assert calls[0][2:4] == ["--default-max-chars", "500"]


def test_speech_timeout_scales_with_text_and_is_capped():
    assert hook._speech_timeout("") == 60
    assert hook._speech_timeout("x" * 1200) == 60 + 100
    # 12,000 chars would want 1,060s; the ceiling wins.
    assert hook._speech_timeout("x" * 12000) == 900


def test_timeout_kills_the_whole_process_group(monkeypatch, tmp_path, capsys):
    path = _write_transcript(tmp_path, [_assistant([_text("hello there")])])
    _patch_main(monkeypatch, {"transcript_path": path})

    class _HangingProc:
        pid = 4242

        def __init__(self):
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="vocalize", timeout=timeout)
            return -9  # the post-kill reap

    proc = _HangingProc()
    monkeypatch.setattr(hook.subprocess, "Popen", lambda argv, **kwargs: proc)
    killed = {}
    monkeypatch.setattr(
        hook.os, "killpg", lambda pgid, sig: killed.update(pgid=pgid, sig=sig)
    )

    assert hook.main() == 0
    # The whole group dies — vocalize AND the afplay child it spawned —
    # and the hook still exits 0 so the session is never blocked.
    assert killed == {"pgid": 4242, "sig": hook.signal.SIGKILL}
    assert proc.waits == 2
    assert "timed out" in capsys.readouterr().err


def test_main_speaks_dash_led_text(monkeypatch, tmp_path):
    # A bulleted reply is the single most common shape Claude produces. Without
    # the "--" separator click reads "- Fixed the parser" as an option and
    # vocalize exits 2 ("No such option"), so nothing is ever spoken.
    reply = "- Fixed the parser\n- Added tests"
    path = _write_transcript(tmp_path, [_assistant([_text(reply)])])
    calls = _patch_main(monkeypatch, {"transcript_path": path})

    assert hook.main() == 0
    argv = calls[0]
    assert argv[-1] == reply
    assert argv[-2] == "--"

    # And prove it against the real parser rather than our idea of it: the
    # same argv tail must get PAST argument parsing. Exit code 2 would mean
    # click rejected the bullet as an unknown option.
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    result = CliRunner().invoke(
        vocalize_cli,
        ["speak", "--max-chars", "500", "--no-play", "--", "- Fixed the parser"],
    )

    assert result.exit_code != 2
    assert "No such option" not in result.output
    # Parsing succeeded, so it runs on and fails at the API-key stage instead.
    assert isinstance(result.exception, MissingAPIKeyError)


def test_nonzero_exit_is_logged_to_stderr(monkeypatch, tmp_path, capsys):
    path = _write_transcript(tmp_path, [_assistant([_text("hello there")])])
    _patch_main(
        monkeypatch,
        {"transcript_path": path},
        run=lambda argv: subprocess.CompletedProcess(argv, 1),
    )

    # A failing vocalize must never block the session, but it must be visible.
    assert hook.main() == 0
    assert "exited 1" in capsys.readouterr().err


def test_main_prefers_vocalize_bin_env(monkeypatch, tmp_path):
    path = _write_transcript(tmp_path, [_assistant([_text("hello there")])])
    calls = _patch_main(monkeypatch, {"transcript_path": path}, which=None)
    monkeypatch.setenv("VOCALIZE_BIN", "/tmp/custom/vocalize")

    assert hook.main() == 0
    assert calls[0][0] == "/tmp/custom/vocalize"


def _fake_projects_dir(tmp_path):
    """Two sessions' transcripts with distinct mtimes; returns (dir, older, newer)."""
    projects = tmp_path / "projects"
    older = projects / "proj-a" / "older.jsonl"
    newer = projects / "proj-b" / "newer.jsonl"
    for path, text in ((older, "older session"), (newer, "newest session")):
        path.parent.mkdir(parents=True)
        path.write_text(_assistant([_text(text)]) + "\n", encoding="utf-8")
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))
    return projects, older, newer


def test_find_latest_transcript_picks_the_newest(tmp_path):
    projects, _older, newer = _fake_projects_dir(tmp_path)

    assert hook._find_latest_transcript(projects) == str(newer)


def test_find_latest_transcript_returns_none_when_nothing_to_find(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert hook._find_latest_transcript(empty) is None
    assert hook._find_latest_transcript(tmp_path / "does-not-exist") is None


def test_latest_mode_speaks_newest_transcript_without_reading_stdin(monkeypatch, tmp_path):
    class ExplodingStdin:
        def read(self, *args):
            raise AssertionError("--latest must not read stdin")

    projects, _older, _newer = _fake_projects_dir(tmp_path)
    calls = _patch_main(monkeypatch, {}, stdin=ExplodingStdin())
    monkeypatch.setattr(hook.sys, "argv", ["claude_stop_hook.py", "--latest"])
    real_find = hook._find_latest_transcript
    monkeypatch.setattr(hook, "_find_latest_transcript", lambda: real_find(projects))

    assert hook.main() == 0
    assert calls[0][-1] == "newest session"


def test_subprocess_failure_is_logged_not_raised(monkeypatch, tmp_path, capsys):
    def boom(argv):
        raise RuntimeError("no audio device")

    path = _write_transcript(tmp_path, [_assistant([_text("hello there")])])
    _patch_main(monkeypatch, {"transcript_path": path}, run=boom)

    assert hook.main() == 0
    assert "vocalize hook" in capsys.readouterr().err
