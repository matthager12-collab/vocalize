#!/usr/bin/env bash
# validate-exit.sh — Run 8a: app-swift
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
check "on its own branch, not main" bash -c 'test "$(git branch --show-current)" != main'
check 'run 7 validated' grep -q '^validate-exit: PASS' docs/plans/2026-09-app-roadmap/run-7-hotkey-spike-and-builder/report.md
check 'DEC-033 decided (no longer Deferred)' grep -qE '^\| DEC-033 \|.*\| Decided \|' docs/plans/2026-09-app-roadmap/decisions.md
check 'run 7 artifact: [app] chord table lands in config.py' grep -q '_validate_app_table' vocalize/config.py
check 'suite green at entry' .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check 'ruff clean at entry' .venv/bin/python -m ruff check vocalize hooks tests

echo ""
echo "=== Exit criteria ==="
check 'menubar source files exist' bash -c 'test -f vocalize/menubar/VocalizeApp.swift && test -f vocalize/menubar/Info.plist.in'
# One file per invocation: swiftc allows top-level code only in a single-file
# compilation, and both of these are single-file programs — parsed together,
# every top-level statement in both is an error, including the frozen
# recorder's. The real build compiles them one at a time (install.py).
check 'app parses' xcrun swiftc -parse vocalize/menubar/VocalizeApp.swift
check 'recorder still parses' xcrun swiftc -parse vocalize/recorder/VocalizeRecorder.swift
check 'plist lints' plutil -lint vocalize/menubar/Info.plist.in
check 'no text can reach a notification' bash -c 'test -f vocalize/menubar/VocalizeApp.swift && ! grep -n "NSPasteboard\.string\|readObjects" vocalize/menubar/VocalizeApp.swift'
check 'override and nonce checks exist (2)' bash -c 'test -f vocalize/menubar/VocalizeApp.swift && test "$(grep -c "func checkedBinary\|func pasteIfNonceMatches" vocalize/menubar/VocalizeApp.swift)" = "2"'
# `git diff HEAD` says nothing about a file git has never seen, and this whole
# directory is new — so the check has to be that both files are tracked and
# unmodified, not that the diff is empty.
check 'source committed' bash -c 'git ls-files --error-unmatch vocalize/menubar/VocalizeApp.swift vocalize/menubar/Info.plist.in >/dev/null && git diff --quiet HEAD -- vocalize/menubar/'
check 'full suite green' .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check 'ruff clean' .venv/bin/python -m ruff check vocalize hooks tests
# Untracked files included: an uncommitted new file is uncommitted work.
check 'work committed' bash -c 'git diff --quiet HEAD && test -z "$(git status --porcelain)"'

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
