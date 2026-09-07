#!/usr/bin/env bash
# validate-exit.sh — Run 11: cue-hold-paste
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
# One more, specific to this run: verification.md's own "-k \"start or
# stop_hold\"" filter already matches over a dozen existing tests whose
# names merely contain "start" (test_the_first_press_starts_a_recorder_...,
# test_the_next_press_after_a_dead_recorder_starts_again, ...) — it would
# PASS today with none of T-101–T-103 built. The hold-to-talk and cue/trim
# checks below are narrowed to substrings that carry zero matches in the
# tests as they stand (verified with grep before relying on them) and are
# drawn from the acceptance criteria's own wording ("idempotent", "_trim_cue").

set -uo pipefail

PASS=0
FAIL=0
TIMEOUT="${CHECK_TIMEOUT:-300}"

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

PREV_REPORT="docs/plans/2026-09-app-roadmap/run-10-release-0-13-0/report.md"
SPIKE_NOTES="docs/plans/2026-09-app-roadmap/spike-notes.md"

echo "=== Entry criteria ==="
check "on its own branch, not main" bash -c 'test "$(git branch --show-current)" != "main"'
check "run 10 (0.13.0 release) validated" bash -c "grep -q '^validate-exit: PASS' '$PREV_REPORT'"
check "0.13.0 wheel exists" bash -c 'ls dist/vocalize_cli-0.13.0*.whl >/dev/null 2>&1'
check "suite green at entry" .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check "ruff clean at entry" .venv/bin/python -m ruff check vocalize hooks tests

echo ""
echo "=== Exit criteria ==="
check "Swift untouched" git diff --quiet main -- vocalize/menubar/
check "cue spike recorded (spike-notes.md § Cue, branch verdict)" bash -c "test -f '$SPIKE_NOTES' && grep -A8 '^## Cue' '$SPIKE_NOTES' | grep -q 'branch:'"
check "no cue word reaches the worker (trim, both branches)" .venv/bin/python -m pytest tests/test_dictate.py -q -k "trim" -p no:cacheprovider
check "hold-to-talk (--start idempotent, --stop never cancels)" .venv/bin/python -m pytest tests/test_dictate.py tests/test_cli.py -q -k "idempotent or hold_to_talk or stop_hold" -p no:cacheprovider
check "paste marker carries the session's nonce" .venv/bin/python -m pytest tests/test_dictate.py -q -k "copied" -p no:cacheprovider
check "full suite green" .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check "ruff clean" .venv/bin/python -m ruff check vocalize hooks tests
check "work committed" git diff --quiet HEAD

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
