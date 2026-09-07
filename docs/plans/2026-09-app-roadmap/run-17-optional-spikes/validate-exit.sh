#!/usr/bin/env bash
# validate-exit.sh — Run 17: optional spikes on no release path
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
# This run has no release-path exit (see plan.md § Phase 17): each of its
# three spikes is optional and throwaway, and none gates a decisions.md row.
# The exit checks below only confirm that a spike the owner actually ran left
# a recorded section in spike-notes.md, and that nothing under vocalize/
# changed — a spike the owner skipped simply has no section, which is a valid
# outcome here, not a defect.

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

echo "=== Entry criteria ==="
# Phase 17 has no dependency edge from any other phase (plan.md § Dependencies)
# and no run in this plan precedes it, so there is no prior report.md or
# prior artifact to check. It starts from main after 0.11.0 like the rest of
# the app-roadmap plan; the only entry bar is that the shared checkout is
# healthy before the owner goes off to do throwaway scratch work with it.
check 'suite green at entry' .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check 'ruff clean at entry' .venv/bin/python -m ruff check vocalize hooks tests

echo ""
echo "=== Exit criteria ==="
check 'T-160 Foundation Models spike recorded' bash -c "test -f docs/plans/2026-09-app-roadmap/spike-notes.md && grep -q '^## Foundation Models' docs/plans/2026-09-app-roadmap/spike-notes.md"
check 'T-161 SpeechAnalyzer spike recorded' bash -c "test -f docs/plans/2026-09-app-roadmap/spike-notes.md && grep -q '^## SpeechAnalyzer' docs/plans/2026-09-app-roadmap/spike-notes.md"
check 'T-162 Voice Memos spike recorded' bash -c "test -f docs/plans/2026-09-app-roadmap/spike-notes.md && grep -q '^## Voice Memos' docs/plans/2026-09-app-roadmap/spike-notes.md"
check 'nothing under vocalize/ changed' git diff --quiet HEAD -- vocalize

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
