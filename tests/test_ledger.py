"""Tests for the local usage ledger. See vocalize/ledger.py's module docstring
for what it's for and why every read/write path swallows its own errors.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vocalize import ledger

JUL = datetime(2026, 7, 3, 10, 0, 0, tzinfo=timezone.utc)
AUG = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
SEPT = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_fresh_dir_reports_zero(tmp_path):
    assert ledger.load(tmp_path) == {}
    assert ledger.status("google", tmp_path) == (0, False)
    assert ledger.all_status(tmp_path) == {}


def test_missing_file_is_silent(tmp_path, capsys):
    assert ledger.load(tmp_path) == {}
    assert capsys.readouterr().err == ""


def test_record_accumulates(tmp_path):
    ledger.record("google", 100, tmp_path, now=SEPT)
    ledger.record("google", 50, tmp_path, now=SEPT)

    assert ledger.status("google", tmp_path, now=SEPT) == (150, False)


def test_record_keeps_providers_separate(tmp_path):
    ledger.record("google", 100, tmp_path, now=SEPT)
    ledger.record("polly", 30, tmp_path, now=SEPT)

    assert ledger.status("google", tmp_path, now=SEPT) == (100, False)
    assert ledger.status("polly", tmp_path, now=SEPT) == (30, False)


def test_record_creates_the_cache_directory_if_missing(tmp_path):
    target = tmp_path / "nested" / "dir"

    ledger.record("google", 5, target, now=SEPT)

    assert ledger.path(target).exists()


def test_month_rollover_keeps_the_previous_month(tmp_path):
    ledger.record("google", 10, tmp_path, now=JUL)
    ledger.record("google", 20, tmp_path, now=AUG)

    data = ledger.load(tmp_path)
    assert set(data["months"]) == {"2026-07", "2026-08"}
    assert ledger.status("google", tmp_path, now=AUG) == (20, False)


def test_month_rollover_prunes_older_months(tmp_path):
    ledger.record("google", 10, tmp_path, now=JUL)
    ledger.record("google", 20, tmp_path, now=AUG)
    ledger.record("google", 30, tmp_path, now=SEPT)

    data = ledger.load(tmp_path)
    assert set(data["months"]) == {"2026-08", "2026-09"}


def test_mark_exhausted_then_status(tmp_path):
    ledger.record("openai", 500, tmp_path, now=SEPT)
    ledger.mark_exhausted("openai", tmp_path, now=SEPT)

    assert ledger.status("openai", tmp_path, now=SEPT) == (500, True)


def test_corrupt_json_becomes_empty_with_one_warning(tmp_path, capsys):
    (tmp_path / ledger.LEDGER_NAME).write_text("not json at all")

    assert ledger.load(tmp_path) == {}
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "usage ledger unreadable" in err


def test_corrupt_ledger_is_overwritten_cleanly_by_the_next_record(tmp_path):
    (tmp_path / ledger.LEDGER_NAME).write_text("not json at all")

    ledger.record("google", 42, tmp_path, now=SEPT)

    assert ledger.status("google", tmp_path, now=SEPT) == (42, False)


@pytest.mark.parametrize(
    "bad_shape",
    [
        json.dumps([1, 2, 3]),
        json.dumps({"version": 1}),  # no "months" key at all
        json.dumps({"version": 1, "months": "nope"}),  # "months" not a dict
    ],
)
def test_wrong_top_level_shape_is_treated_as_corrupt(tmp_path, capsys, bad_shape):
    (tmp_path / ledger.LEDGER_NAME).write_text(bad_shape)

    assert ledger.load(tmp_path) == {}
    assert "usage ledger unreadable" in capsys.readouterr().err


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permission bits")
def test_unwritable_dir_warns_and_leaves_no_tmp_file(tmp_path, capsys):
    target = tmp_path / "locked"
    target.mkdir()
    target.chmod(0o500)  # read+execute only: mkdir(exist_ok=True) still works, writing doesn't
    try:
        ledger.record("google", 10, target, now=SEPT)  # must not raise

        err = capsys.readouterr().err
        assert "budget tracking unavailable" in err
        assert list(target.iterdir()) == []  # no .tmp left behind
    finally:
        target.chmod(0o700)  # let pytest clean up tmp_path afterwards


def test_file_mode_is_0600_after_a_successful_write(tmp_path):
    ledger.record("google", 10, tmp_path, now=SEPT)

    mode = stat.S_IMODE(ledger.path(tmp_path).stat().st_mode)
    assert mode == 0o600


def test_no_tmp_file_remains_after_a_successful_write(tmp_path):
    ledger.record("google", 10, tmp_path, now=SEPT)

    assert not (tmp_path / (ledger.LEDGER_NAME + ".tmp")).exists()


def test_negative_chars_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        ledger.record("google", -1, tmp_path, now=SEPT)

    assert ledger.load(tmp_path) == {}  # rejected before anything was written


def test_all_status_only_reports_the_current_month(tmp_path):
    ledger.record("google", 10, tmp_path, now=AUG)
    ledger.record("google", 20, tmp_path, now=SEPT)
    ledger.record("polly", 5, tmp_path, now=SEPT)

    assert ledger.all_status(tmp_path, now=SEPT) == {
        "google": {"chars": 20, "exhausted": False},
        "polly": {"chars": 5, "exhausted": False},
    }


def test_month_key_is_local_time_year_month():
    assert ledger.month_key(SEPT) == "2026-09"


def test_unknown_provider_names_are_accepted(tmp_path):
    ledger.record("some-future-provider", 7, tmp_path, now=SEPT)

    assert ledger.status("some-future-provider", tmp_path, now=SEPT) == (7, False)


def test_autouse_fixture_redirects_the_default_ledger_path(tmp_path):
    """`_no_real_ledger` in conftest.py must be doing its job.

    A silent miss here would mean every other test in this file that
    happens to call the default-path form is quietly hitting the real
    ~/.cache/vocalize instead of tmp_path.
    """
    real_home_ledger = Path.home() / ".cache" / "vocalize" / ledger.LEDGER_NAME
    # The developer's real ledger may legitimately exist (vocalize has been
    # used on this machine); what must hold is that this test never touches
    # it. Compare its identity before and after, absent or present.
    before = (
        (real_home_ledger.stat().st_mtime_ns, real_home_ledger.stat().st_size)
        if real_home_ledger.exists() else None
    )

    ledger.record("google", 1, now=SEPT)  # cache_dir=None -> default dir

    assert ledger.DEFAULT_CACHE_DIR == tmp_path
    assert ledger.path().exists()
    after = (
        (real_home_ledger.stat().st_mtime_ns, real_home_ledger.stat().st_size)
        if real_home_ledger.exists() else None
    )
    assert after == before
