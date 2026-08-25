import io
import json
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


def _patch_main(monkeypatch, payload, which="/usr/local/bin/vocalize", run=None):
    """Wire up stdin, PATH lookup and subprocess so main() can't shell out."""
    monkeypatch.delenv("VOCALIZE_MAX_CHARS", raising=False)
    monkeypatch.delenv("VOCALIZE_BIN", raising=False)
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(hook.shutil, "which", lambda name: which)

    calls = []

    def fake_run(argv, **kwargs):
        # Pin the timeout: a Stop hook that can hang forever blocks the
        # session, so this is behaviour, not an implementation detail.
        assert kwargs.get("timeout") == 60
        calls.append(argv)
        if run is not None:
            return run(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
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
            "--max-chars",
            "500",
            "--play",
            "--",
            "hello there",
        ]
    ]


def test_main_honours_vocalize_max_chars_env(monkeypatch, tmp_path):
    path = _write_transcript(tmp_path, [_assistant([_text("hello there")])])
    calls = _patch_main(monkeypatch, {"transcript_path": path})
    monkeypatch.setenv("VOCALIZE_MAX_CHARS", "120")

    assert hook.main() == 0
    assert calls == [
        [
            "/usr/local/bin/vocalize",
            "speak",
            "--max-chars",
            "120",
            "--play",
            "--",
            "hello there",
        ]
    ]


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


def test_subprocess_failure_is_logged_not_raised(monkeypatch, tmp_path, capsys):
    def boom(argv):
        raise RuntimeError("no audio device")

    path = _write_transcript(tmp_path, [_assistant([_text("hello there")])])
    _patch_main(monkeypatch, {"transcript_path": path}, run=boom)

    assert hook.main() == 0
    assert "vocalize hook" in capsys.readouterr().err
