#!/usr/bin/env bash
# validate-exit.sh — Run 15b: recording pause and resume
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
check 'run 15 (notes) validated' grep -q '^validate-exit: PASS' docs/plans/2026-09-app-roadmap/run-15-notes/report.md
check "run 15's key artifact present: vocalize/notes.py § _done" bash -c "grep -qE '^def _done\(' vocalize/notes.py"
check 'DEC-037 Decided' bash -c "grep -A5 '^### DEC-037' docs/plans/2026-09-app-roadmap/decisions.md | grep -q 'Status.*Decided'"
check "[app] stop_hotkey shipped in 0.13.1" bash -c "grep -q 'stop_hotkey' vocalize/config.py"
check 'vocalize/recorder/ matches main (nothing to re-grant)' git diff --quiet main -- vocalize/recorder/
check 'vocalize/menubar/ matches main (nothing to re-grant)' git diff --quiet main -- vocalize/menubar/
check 'suite green at entry' .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check 'ruff clean at entry' .venv/bin/python -m ruff check vocalize hooks tests

echo ""
echo "=== Exit criteria ==="
check 'dictate carries --pause and --resume' bash -c ".venv/bin/vocalize dictate --help | grep -q -- '--pause' && .venv/bin/vocalize dictate --help | grep -q -- '--resume'"
check_output 'pause and resume mechanics' 'passed' .venv/bin/python -m pytest tests/test_dictate.py::test_pause_finalises_a_segment_and_leaves_the_session_claimed tests/test_dictate.py::test_resume_launches_a_second_recorder_with_the_remaining_budget tests/test_dictate.py::test_resume_max_never_falls_below_one_second -q -p no:cacheprovider
check_output 'backstop and marker are safe against a stale or corrupt take' 'passed' .venv/bin/python -m pytest tests/test_dictate.py::test_wait_for_exit_backstop_uses_the_segment_start_not_the_take_start tests/test_dictate.py::test_resume_treats_a_corrupt_paused_marker_as_no_pause -q -p no:cacheprovider
check_output 'a self-stopped segment notifies rather than closing silently' 'passed' .venv/bin/python -m pytest tests/test_dictate.py::test_segment_self_stop_at_max_notifies_before_the_mic_closes -q -p no:cacheprovider
check_output 'transcription budget scales with the take' 'passed' .venv/bin/python -m pytest tests/test_dictate.py::test_transcribe_timeout_scales_with_take_length -q -p no:cacheprovider
check_output 'session state stays recording while paused' 'passed' .venv/bin/python -m pytest tests/test_dictate.py::test_session_state_stays_recording_while_paused -q -p no:cacheprovider
check_output 'budgets bound the take' 'passed' bash -c "grep -q 'max_take_seconds' vocalize/config.py && .venv/bin/python -m pytest tests/test_dictate.py::test_resume_refuses_a_twenty_first_segment tests/test_dictate.py::test_resume_refuses_past_the_take_budget tests/test_config.py::test_max_take_seconds_bounds -q -p no:cacheprovider"
check_output 'segments join losslessly' 'passed' .venv/bin/python -m pytest tests/test_dictate.py::test_join_segments_frames_and_silence tests/test_dictate.py::test_joined_wav_keeps_16k_mono_16bit tests/test_dictate.py::test_cue_trimmed_per_segment_never_reaches_the_worker -q -p no:cacheprovider
check_output 'a paused take survives the next press' 'passed' .venv/bin/python -m pytest tests/test_dictate.py::test_toggle_while_paused_stops_and_transcribes tests/test_dictate.py::test_cancel_while_paused_removes_every_segment tests/test_dictate.py::test_paused_workdir_younger_than_24h_is_not_swept -q -p no:cacheprovider
check_output 'stop precedence' 'passed' .venv/bin/python -m pytest tests/test_cli.py::test_stop_pauses_a_live_recording_before_playback tests/test_cli.py::test_stop_resumes_a_paused_recording tests/test_cli.py::test_unknown_session_state_falls_through_to_playback -q -p no:cacheprovider
check 'recorder untouched (no new microphone grant)' git diff --quiet main -- vocalize/recorder/
check 'Swift app untouched (no re-grant)' git diff --quiet main -- vocalize/menubar/
check 'docs carry the pause flag and the budget' bash -c "grep -q 'dictate --pause' docs/dictation.md && grep -q 'max_take_seconds' docs/dictation.md && grep -q 'dictate --pause' CHANGELOG.md"
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
