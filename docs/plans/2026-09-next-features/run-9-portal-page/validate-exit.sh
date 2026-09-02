#!/usr/bin/env bash
# validate-exit.sh — Run 9: portal-page
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
check 'on branch config-portal' .venv/bin/python -c 'import subprocess,sys; sys.exit(subprocess.run(['"'"'git'"'"','"'"'branch'"'"','"'"'--show-current'"'"'],capture_output=True,text=True).stdout.strip()!='"'"'config-portal'"'"')'
check 'run 8 validated' grep -q '^validate-exit: PASS' docs/plans/2026-09-next-features/run-8-portal-write/report.md
check 'portal command present' .venv/bin/vocalize portal --help
check 'suite green at entry' .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider

echo ""
echo "=== Exit criteria ==="
check 'assets exist' .venv/bin/python -c 'import os; assert os.path.exists('"'"'vocalize/assets/portal.html'"'"') and os.path.exists('"'"'vocalize/assets/portal.js'"'"')'
check 'no inline script in the page' .venv/bin/python -c 'import pathlib,re; t=pathlib.Path('"'"'vocalize/assets/portal.html'"'"').read_text(); assert not re.search(r'"'"'<script(?![^>]*\ssrc=)[^>]*>'"'"', t), '"'"'inline <script> found'"'"''
check 'no external URL in page or script' .venv/bin/python -c 'import pathlib,re; t=pathlib.Path('"'"'vocalize/assets/portal.html'"'"').read_text()+pathlib.Path('"'"'vocalize/assets/portal.js'"'"').read_text(); assert not re.search(r'"'"'https?://'"'"', t), '"'"'external URL found'"'"''
check 'page served with headers (tests)' .venv/bin/python -m pytest tests/test_portal.py -k 'page or static or assets' -q -p no:cacheprovider
check 'assets ship in the wheel' .venv/bin/python -c 'import subprocess,tempfile,glob,sys,zipfile; d=tempfile.mkdtemp(); subprocess.run([sys.executable,'"'"'-m'"'"','"'"'build'"'"','"'"'--wheel'"'"','"'"'-o'"'"',d],check=True,capture_output=True); names=zipfile.ZipFile(glob.glob(d+'"'"'/vocalize_cli-*.whl'"'"')[0]).namelist(); assert any(n.endswith('"'"'assets/portal.js'"'"') for n in names), names[:20]'
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
