#!/usr/bin/env bash
# validate-exit.sh — Run 11b: playback-pause
#
# Checks this run's entry and exit criteria. Exits 0 only if every check passed.
# Generated from the split-plan skill template. RUN IT before handing the run
# over — once with a check you expect to pass, once with one you expect to fail.
#
# Two inherited bugs this template exists to avoid:
#   1. ((PASS++)) evaluates to 0 on the first increment, which `set -e` treats
#      as failure and aborts on. Always use PASS=$((PASS+1)).
#   2. eval'ing a criterion string with a substring match passes on noise and
#      hangs forever on a stalled command. Use exit status and a timeout.
#   3. A check that cannot fail yet proves nothing: pre-build, every check
#      for a not-yet-built artifact must FAIL. Chain an existence
#      precondition before any property query (a missing table "has no
#      violations"), use exact file paths over test filters, scope greps to
#      the specific future row. (Defect #9, observed live 2026-08-22.)
#
# A fourth trap, found writing this script: `pytest -k <keyword>` against a
# test file that already exists and has other tests exits 0 ("N deselected")
# when the keyword matches nothing yet — a silent vacuous pass. check_output
# is used for those rows instead, requiring the word "passed" in the output,
# so a pre-build run with zero matching tests correctly fails.

set -uo pipefail

PASS=0
FAIL=0
TIMEOUT="${CHECK_TIMEOUT:-120}"

# Portable timeout: GNU coreutils on Linux, gtimeout via brew on macOS, or none.
# The no-timeout fallback is `env`, which just runs the command — an empty array
# would expand to an unbound-variable error under `set -u` on bash 3.2 (macOS).
if command -v timeout >/dev/null 2>&1; then
  RUN_TIMEOUT=(timeout "$TIMEOUT")
elif command -v gtimeout >/dev/null 2>&1; then
  RUN_TIMEOUT=(gtimeout "$TIMEOUT")
else
  RUN_TIMEOUT=(env)
fi

# check <description> <command> [args...]
# Passes when the command exits 0. No eval, no substring matching.
check() {
  local desc="$1"; shift
  local output status
  if output=$("${RUN_TIMEOUT[@]}" "$@" 2>&1); then
    echo "PASS: $desc"
    PASS=$((PASS + 1))
  else
    status=$?
    if [[ $status -eq 124 ]]; then
      echo "FAIL: $desc (timed out after ${TIMEOUT}s)"
    else
      echo "FAIL: $desc (exit $status)"
      [[ -n "$output" ]] && echo "$output" | tail -5 | sed 's/^/      /'
    fi
    FAIL=$((FAIL + 1))
  fi
}

# check_output <description> <expected-substring> <command> [args...]
# Use only when exit status cannot express the criterion. Prefer check().
check_output() {
  local desc="$1" expected="$2"; shift 2
  local output
  if ! output=$("${RUN_TIMEOUT[@]}" "$@" 2>&1); then
    echo "FAIL: $desc (command failed)"
    FAIL=$((FAIL + 1))
    return
  fi
  if [[ "$output" == *"$expected"* ]]; then
    echo "PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $desc (expected to find '$expected')"
    FAIL=$((FAIL + 1))
  fi
}

# Every path below is relative to the repository root.
cd "$(cd "$(dirname "$0")" && git rev-parse --show-toplevel)" || exit 1

echo "=== Entry criteria ==="
check 'on its own branch (not main)' bash -c 'test "$(git branch --show-current)" != main'
check 'run 11 (cue-hold-paste) validated' grep -q '^validate-exit: PASS' docs/plans/2026-09-app-roadmap/run-11-cue-hold-paste/report.md
check 'run 11 key artifact: dictate --start/--stop (T-102, present on both T-101 branches)' bash -c "grep -q -- '--start' vocalize/cli.py && grep -q -- '--stop' vocalize/cli.py"
check 'DEC-036 Decided (how a read pauses, and how it is reached)' bash -c "grep -A5 '^### DEC-036' docs/plans/2026-09-app-roadmap/decisions.md | grep -q 'Status.*Decided'"
check 'vocalize/menubar/ matches main (nothing to re-grant)' git diff --quiet main -- vocalize/menubar/
check 'vocalize/recorder/ matches main (nothing to re-grant)' git diff --quiet main -- vocalize/recorder/
check 'suite green at entry' .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check 'ruff clean at entry' .venv/bin/python -m ruff check vocalize hooks tests

echo ""
echo "=== Exit criteria ==="
check 'vocalize pause exists as a command' bash -c "grep -qE '^def pause\(' vocalize/cli.py"
check 'vocalize pause is a registered command' .venv/bin/vocalize pause --help
check_output 'pause saves the record and reports honestly' 'passed' .venv/bin/python -m pytest tests/test_cli.py::test_pause_saves_the_record_like_a_dictation tests/test_cli.py::test_pause_with_nothing_playing_reports_it tests/test_cli.py::test_pause_in_the_chunk_gap_records_the_queued_piece -q -p no:cacheprovider
check 'wait_for_record moved to interrupted.py and dictate calls it' bash -c "grep -qE '^def wait_for_record\(' vocalize/interrupted.py && grep -q 'interrupted.wait_for_record' vocalize/dictate.py"
check_output 'stop_hotkey validates, routes, and is printed by settings' 'passed' .venv/bin/python -m pytest tests/test_config.py::test_stop_hotkey_rejects_an_unknown_word tests/test_cli.py::test_stop_hotkey_pause_pauses_then_resumes tests/test_cli.py::test_stop_hotkey_pause_never_resumes_while_a_dictation_is_live tests/test_cli.py::test_settings_prints_stop_hotkey -q -p no:cacheprovider
check_output 'plain stop unchanged' 'passed' .venv/bin/python -m pytest tests/test_cli.py::test_plain_stop_records_nothing_and_never_resumes -q -p no:cacheprovider
check_output 'resume rewinds one second' 'passed' bash -c "grep -q '_RESUME_REWIND' vocalize/interrupted.py && .venv/bin/python -m pytest tests/test_dictate.py::test_resume_rewinds_one_second_before_the_pause_point -q -p no:cacheprovider"
check_output 'failure modes pinned' 'passed' .venv/bin/python -m pytest tests/test_dictate.py::test_dictation_while_paused_never_offers_the_paused_read tests/test_dictate.py::test_resume_with_an_installed_but_unusable_provider_reports_and_keeps_the_record tests/test_cli.py::test_two_resumes_do_not_corrupt_the_record -q -p no:cacheprovider
check 'Swift untouched (no re-grant)' git diff --quiet main -- vocalize/menubar/
check 'recorder untouched (no new microphone grant)' git diff --quiet main -- vocalize/recorder/
check 'docs name the pause verb' bash -c "grep -q 'vocalize pause' README.md && grep -q 'vocalize pause' docs/dictation.md && grep -q 'vocalize pause' CHANGELOG.md"
check 'full suite green' .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check 'ruff clean' .venv/bin/python -m ruff check vocalize hooks tests
check 'work committed' git diff --quiet HEAD

echo ""
echo "=== Summary ==="
TOTAL=$((PASS + FAIL))
echo "Passed: $PASS / $TOTAL"
echo "Failed: $FAIL / $TOTAL"

if [[ $TOTAL -eq 0 ]]; then
  echo "NO CHECKS DEFINED — this script proves nothing"
  exit 1
fi

if [[ $FAIL -eq 0 ]]; then
  echo "ALL CHECKS PASSED"
  exit 0
fi

echo "SOME CHECKS FAILED"
exit 1
