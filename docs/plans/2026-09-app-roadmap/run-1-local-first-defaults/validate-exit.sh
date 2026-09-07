#!/usr/bin/env bash
# validate-exit.sh — Run 1: local-first-defaults
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
check "on its own branch (not main)" bash -c 'test "$(git branch --show-current)" != main'
# No previous run precedes run-1 in this plan (Phase 1 is the first phase),
# so there is no prior report.md or key artifact to check.
check "suite green at entry" .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check "ruff clean at entry" .venv/bin/python -m ruff check vocalize hooks tests

echo ""
echo "=== Exit criteria ==="

# Phase 1 exit row: "Default chain flipped and noted". Pre-build this matches
# zero tests (pytest exits 5, no tests collected) because the old test names
# (test_chain_defaults_to_elevenlabs_then_say, etc.) don't carry these
# substrings yet — T-02 renames/adds them.
check "default chain flipped and fallback note tested" \
  .venv/bin/python -m pytest tests/test_config.py tests/test_chain.py tests/test_cli.py \
    -q -k "default_chain or fallback_note" -p no:cacheprovider

# Phase 1 exit row: "Fresh machine still speaks". On an empty config with no
# Kokoro model installed, `vocalize speak` must fall back and stderr must
# name Kokoro's missing install exactly once. Pre-build the default chain is
# still ("elevenlabs", "say"), so this string never appears (count 0, not 1).
check "fresh machine speaks via say with the Kokoro note" bash -c '
  H=$(mktemp -d) || exit 1
  HOME="$H" .venv/bin/vocalize speak "hello" >/dev/null 2>"$H/err.log"
  c=$(grep -c "Kokoro is not installed" "$H/err.log")
  test "$c" = "1"
'

# Phase 1 exit row: "Docs lead with local" — no ElevenLabs mention in
# README.md before the "## Providers" heading. Pre-build, ElevenLabs is
# mentioned repeatedly (title, quickstart, key section) before that heading.
check "docs lead with local (no ElevenLabs before ## Providers)" \
  awk '/^## Providers/{exit} /ElevenLabs/{f=1} END{exit f}' README.md

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
