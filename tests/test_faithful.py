import json
from pathlib import Path

from vocalize import llm


def test_recorded_good_outputs_are_faithful():
    rows = json.loads((Path(__file__).parent / "fixtures" / "s2_cleanup_outputs.json").read_text())
    assert all(llm.faithful(row["raw"], row["output"]) for row in rows if row["judge_verdict"] == "PASS")


def test_guard_catches_meta_commentary():
    rows = json.loads((Path(__file__).parent / "fixtures" / "s2_cleanup_outputs.json").read_text())
    row = next(row for row in rows if row["prompt_id"] == "P2" and row["case_id"] == "I2")
    assert not llm.faithful(row["raw"], row["output"])


def test_guard_keeps_negations_unless_corrected():
    raw = "I do not think we should ship"
    assert not llm.faithful(raw, "I think we should ship")
    assert not llm.faithful("I don't think we should ship", "I think we should ship")
    assert llm.faithful("Move it to Tuesday, no, Wednesday", "Move it to Wednesday")


def test_guard_allows_spoken_formatting_but_not_new_email():
    assert llm.faithful("meet march third at nine thirty", "Meet March 3 at 9:30")
    assert llm.faithful("reach sam at sam at example dot com", "Reach Sam at sam@example.com")
    assert not llm.faithful("email bob", "Email bob@evil.com")


def test_unexpected_stop_reason_is_not_echoed(monkeypatch, capsys):
    monkeypatch.setattr(
        llm._http, "request",
        lambda *args, **kwargs: (200, b'{"content": [], "stop_reason": "CANARY-SECRET-123"}'),
    )
    assert llm._anthropic("key", "system", "text", 1, 1, "cleanup") is None
    assert "CANARY-SECRET-123" not in capsys.readouterr().err
