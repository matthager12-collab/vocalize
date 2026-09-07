#!/usr/bin/env bash
# validate-exit.sh — Run 9: doctor-integrate-setup
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

# A fixed scratch HOME for the two integrate checks below, so the second
# check can inspect the files the first check's run produced instead of
# re-running the installer (and instead of guessing where a shared
# `mktemp -d` would go under `set -u`).
SCRATCH_HOME=$(mktemp -d 2>/dev/null || echo /tmp/vocalize-run9-scratch-home)
WFLOW="$SCRATCH_HOME/Library/Services/Speak with Vocalize.workflow/Contents/Resources/document.wflow"

echo "=== Entry criteria ==="
check "on its own branch, not main" bash -c 'test "$(git branch --show-current)" != main'
check 'run 8b validated' grep -q '^validate-exit: PASS' docs/plans/2026-09-app-roadmap/run-8b-app-python/report.md
check 'run 8b artifact: app status reports bundle/agent' bash -c '.venv/bin/vocalize app status --json | python3 -c "import json,sys; d=json.load(sys.stdin); assert \"bundle\" in d and \"agent\" in d"'
check 'suite green at entry' .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check 'ruff clean at entry' .venv/bin/python -m ruff check vocalize hooks tests

echo ""
echo "=== Exit criteria ==="
check 'doctor lists every required row' bash -c '.venv/bin/vocalize doctor --json | python3 -c "import json,sys; rows=json.load(sys.stdin); names={r[\"name\"] for r in rows}; assert {\"cli path\",\"uv\",\"swiftc\",\"claude\"} <= names, names"'
check 'integrate on a scratch home' bash -c "HOME='$SCRATCH_HOME' .venv/bin/vocalize integrate claude --yes && test -f '$SCRATCH_HOME/.claude/skills/speak/SKILL.md' && test \"\$(ls '$SCRATCH_HOME/Library/Services' 2>/dev/null | grep -c workflow)\" = 4"
check 'baked claude path is the symlink, not its resolved target' bash -c "test -f '$WFLOW' && grep -q '/opt/homebrew/bin/claude' '$WFLOW' && ! grep -q 'Caskroom' '$WFLOW'"
check 'setup tab tests' .venv/bin/python -m pytest tests/test_portal.py -q -k setup -p no:cacheprovider
check 'page discipline: no inline script' bash -c 'test -f vocalize/assets/portal.html && ! grep -q "<script>" vocalize/assets/portal.html'
check 'page discipline: no external URL' bash -c '! grep -E "https?://" vocalize/assets/portal.html vocalize/assets/portal.js'
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
