#!/usr/bin/env bash
# validate-exit.sh — Run 8: portal-write
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
# 600, not the template's 120: two of the checks below run the whole suite,
# which takes 115-121s on this machine. A 120s cap sits inside that spread,
# so those two checks were a coin flip between "green" and "timed out after
# 120s" — a gate that reports the machine's mood rather than the branch's
# state. See DEC-019.
TIMEOUT="${CHECK_TIMEOUT:-600}"

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

# The isolation criterion, as substance rather than as a literal name. It
# was written `on branch config-portal`, and this run is on `portal-writes`,
# so it failed on every commit and the gate never passed as written. What
# the criterion is actually for is two facts: the work is on its own branch
# (not on main), and that branch forked from a commit that already carries
# run 7's portal module. Both are checked; a rename cannot satisfy either.
# See DEC-019.
ISOLATED='
import subprocess, sys

def git(*args):
    return subprocess.run(("git",) + args, capture_output=True, text=True)

branch = git("branch", "--show-current").stdout.strip()
base = git("merge-base", "main", "HEAD").stdout.strip()
sys.exit(not (
    branch not in ("", "main")
    and base
    and git("cat-file", "-e", base + ":vocalize/portal.py").returncode == 0
))
'

echo "=== Entry criteria ==="
check "on its own branch, forked from run 7's merged state" .venv/bin/python -c "$ISOLATED"
check 'run 7 validated' grep -q '^validate-exit: PASS' docs/plans/2026-09-next-features/run-7-portal-read/report.md
check 'portal module present' test -f vocalize/portal.py
check 'suite green at entry' .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider

echo ""
echo "=== Exit criteria ==="
check 'compare-and-swap helper exists' grep -qE '^def write_config_if_unchanged' vocalize/wizard.py
check 'writes are safe (cas, absent sentinel, login never echoes key)' .venv/bin/python -m pytest tests/test_portal.py -k 'write or cas or login' -q -p no:cacheprovider
check 'preview respects budgets, cache and the lock' .venv/bin/python -m pytest tests/test_portal.py -k preview -q -p no:cacheprovider
check 'install thread progress' .venv/bin/python -m pytest tests/test_portal.py -k install -q -p no:cacheprovider
check 'vocalize portal command exists' .venv/bin/vocalize portal --help
check 'chain setter and wizard still green with the helper' .venv/bin/python -m pytest tests/test_cli.py tests/test_wizard.py -k 'chain or wizard or config' -q -p no:cacheprovider
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
