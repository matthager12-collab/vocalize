#!/usr/bin/env bash
# validate-exit.sh — Run 4: keychain
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
check "on its own branch (not main)" bash -c 'test "$(git branch --show-current)" != main'
check "run 3 (llm.py and enums) validated" \
  grep -q '^validate-exit: PASS' docs/plans/2026-09-app-roadmap/run-3-llm-and-enums/report.md
check "run 3 key artifact exists (vocalize/llm.py)" test -f vocalize/llm.py
check "suite green at entry" .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check "ruff clean at entry" .venv/bin/python -m ruff check vocalize hooks tests

echo ""
echo "=== Exit criteria ==="

# Phase 4 exit row: "Check recorded" — DEC-035 must carry Status: Decided.
# Pre-build the entry reads "**Status**: Deferred", so this fails.
check "DEC-035 recorded as Decided" bash -c \
  "grep -A5 '^### DEC-035' docs/plans/2026-09-app-roadmap/decisions.md | grep -q 'Status.*Decided'"

# Phase 4 exit rows: "Backend (branch A)" and "Docs (branch B)" are the same
# underlying criterion — the keychain backend matches whichever branch
# DEC-035 actually decided — so one check dispatches on the recorded
# decision rather than running both rows unconditionally (a run that lands
# on branch B would otherwise fail row 2 forever, and vice versa). Pre-build
# DEC-035 reads "**Decision**: Pending the 30-minute check.", which matches
# neither branch, so this correctly fails until T-30/T-31 land.
check "keychain backend matches the branch DEC-035 decided" bash -c '
  block=$(grep -A20 "^### DEC-035" docs/plans/2026-09-app-roadmap/decisions.md)
  if echo "$block" | grep -qF "**Decision**: A"; then
    .venv/bin/python -m pytest tests/test_auth.py -q -k security -p no:cacheprovider
  elif echo "$block" | grep -qF "**Decision**: B"; then
    test -f docs/provider-credentials.md && grep -q "Always Allow" docs/provider-credentials.md
  else
    echo "DEC-035 not yet decided A or B" >&2
    exit 1
  fi
'

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
