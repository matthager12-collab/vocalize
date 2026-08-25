import io
import json
import subprocess

import claude_stop_hook as hook


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
    path = _write_transcript(
        tmp_path,
        [
            _assistant([_text("older answer")]),
            "{not json at all",
            _assistant([_text("newest answer")]),
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
            "hello there",
            "--max-chars",
            "500",
            "--play",
        ]
    ]


def test_main_honours_vocalize_max_chars_env(monkeypatch, tmp_path):
    path = _write_transcript(tmp_path, [_assistant([_text("hello there")])])
    calls = _patch_main(monkeypatch, {"transcript_path": path})
    monkeypatch.setenv("VOCALIZE_MAX_CHARS", "120")

    assert hook.main() == 0
    assert calls[0][calls[0].index("--max-chars") + 1] == "120"


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
