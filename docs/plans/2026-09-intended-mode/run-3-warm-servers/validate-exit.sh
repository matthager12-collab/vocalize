#!/usr/bin/env bash
# validate-exit.sh — Run 3: Phase 3 part 1 (T-30 to T-33): the servers and the client, not yet wired in
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
TIMEOUT="${CHECK_TIMEOUT:-2400}"  # the suite takes ~160 s, the evals minutes

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


# Run from anywhere: checks resolve against the repository root.
ROOT="${VOCALIZE_ROOT:-$(git rev-parse --show-toplevel)}"
cd "$ROOT" || exit 2
export PY="${VOCALIZE_PY:-python}"
# `check` runs its command under `timeout`, which cannot call a shell
# function, so every pytest call is spelled out. An exact test file always
# carries an existence precondition, so a missing file FAILs instead of
# collecting nothing.

echo "=== Entry criteria ==="
check "run 2 merged: vocabulary on main" grep -q '"vocabulary"' vocalize/config.py

echo ""
echo "=== Exit criteria ==="
check "T-30 protocol file" test -f vocalize/local/warm_protocol.py
check "T-30 protocol imports nothing from vocalize" bash -c 'test -f vocalize/local/warm_protocol.py && ! grep -Eq "^(from|import) vocalize|^from \\." vocalize/local/warm_protocol.py'
check "T-30 protocol tests" bash -c 'test -f tests/test_warm_protocol.py && "$PY" -m pytest -q -p no:cacheprovider tests/test_warm_protocol.py'
check "T-31 whisper serve tests" bash -c 'test -f tests/test_whisper_serve.py && "$PY" -m pytest -q -p no:cacheprovider tests/test_whisper_serve.py'
check "T-32 llm serve tests" bash -c 'test -f tests/test_llm_serve.py && "$PY" -m pytest -q -p no:cacheprovider tests/test_llm_serve.py'
check "T-32 no decode round trip" bash -c '! grep -q "tokenizer.decode(" vocalize/local/llm_worker.py'
check "T-33 client tests" bash -c 'test -f tests/test_warm_client.py && "$PY" -m pytest -q -p no:cacheprovider tests/test_warm_client.py'
check "lint clean" ruff check .
check "whole unit suite green" "$PY" -m pytest -q -p no:cacheprovider

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
