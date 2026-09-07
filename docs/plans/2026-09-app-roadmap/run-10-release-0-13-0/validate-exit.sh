#!/usr/bin/env bash
# validate-exit.sh — Run 10: release-0-13-0
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
# The full suite runs the whole tests/ tree twice below (entry + exit); give
# it real headroom over the 120s default rather than risk a coin-flip timeout.
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
check "run 9 (doctor/integrate/setup) validated" grep -q '^validate-exit: PASS' docs/plans/2026-09-app-roadmap/run-9-doctor-integrate-setup/report.md
check "run 9's key artifact present (readiness.doctor_rows)" grep -q "def doctor_rows" vocalize/readiness.py
check "suite green at entry" .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check "ruff clean at entry" .venv/bin/python -m ruff check vocalize hooks tests

echo ""
echo "=== Exit criteria ==="
check "review file review-0.13.0.md exists" test -f docs/plans/2026-09-app-roadmap/review-0.13.0.md
check "no open critical/high finding" .venv/bin/python -c 'import re,pathlib; t=pathlib.Path("docs/plans/2026-09-app-roadmap/review-0.13.0.md").read_text(); assert not re.search(r"^\| *(critical|high) *\|.*\| *open *\|", t, re.I|re.M), "open critical/high finding"'
check "CHANGELOG has 0.13.0" grep -qE '^## .*0\.13\.0' CHANGELOG.md
check "version bumped to 0.13.0" grep -qE '^__version__ = "0\.13\.0"' vocalize/__init__.py
check "docs match the CLI (0.13.0 commands)" .venv/bin/python -c 'import subprocess; [subprocess.run([".venv/bin/vocalize", *c.split(), "--help"], check=True, capture_output=True) for c in ("listen", "dictate", "resume", "status", "doctor", "app install", "app status", "integrate claude", "local install", "auth login")]'
check "full suite green" .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check "ruff clean" .venv/bin/python -m ruff check vocalize hooks tests
check "work committed" git diff --quiet HEAD
check "package builds the 0.13.0 wheel" bash -c 'rm -f dist/vocalize_cli-0.13.0*; .venv/bin/python -m build >/dev/null 2>&1; ls dist/vocalize_cli-0.13.0*.whl >/dev/null 2>&1'
check "clean-venv install has no leaked ML runtime" bash -c '
  set -e
  rm -rf /tmp/vocalize-0-13-0-cleanvenv
  python3 -m venv /tmp/vocalize-0-13-0-cleanvenv
  /tmp/vocalize-0-13-0-cleanvenv/bin/pip install -q --no-cache-dir dist/vocalize_cli-0.13.0*.whl
  ! /tmp/vocalize-0-13-0-cleanvenv/bin/pip list | grep -iE "pywhispercpp|onnxruntime|mlx|sherpa|numpy|torch|boto3"
'
check "PyPI 0.13.0 published with matching digest (after the owner publishes)" .venv/bin/python -c 'import json,urllib.request,hashlib,glob,sys; local={hashlib.sha256(open(f,"rb").read()).hexdigest() for f in glob.glob("dist/vocalize_cli-0.13.0*")}; data=json.load(urllib.request.urlopen("https://pypi.org/pypi/vocalize-cli/0.13.0/json", timeout=20)); remote={u["digests"]["sha256"] for u in data["urls"]}; assert local and local==remote, (local, remote)'

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
