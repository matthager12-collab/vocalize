#!/usr/bin/env bash
# validate-exit.sh — Run 3: llm.py and enums
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

ROADMAP_DIR="docs/plans/2026-09-app-roadmap"

# --- python snippets, kept as heredoc-style variables so quoting stays sane ---

# [stt] cleanup enum + coercion + verbatim key threading (T-22). Currently
# `cleanup` is validated as a strict bool and `verbatim` is not in
# KNOWN_STT_KEYS at all, so every assertion below fails pre-build.
STT_ENUM_CHECK='
from vocalize import config

resolved = config.resolve_stt({"stt": {"cleanup": True}})
assert resolved["cleanup"] == "claude-cli", f"legacy true did not coerce: {resolved!r}"

resolved = config.resolve_stt({"stt": {"cleanup": False}})
assert resolved["cleanup"] == "off", f"legacy false did not coerce: {resolved!r}"

resolved = config.resolve_stt({"stt": {"cleanup": "local", "verbatim": True}})
assert resolved["cleanup"] == "local", f"enum value local not accepted: {resolved!r}"
assert resolved.get("verbatim") is True, f"verbatim key not threaded: {resolved!r}"
'

# Issue #5 (T-26): a non-string/oversized provider value must be a ConfigError.
# Today only monthly_chars is type-checked, so voice=12345 is silently accepted.
PROVIDER_TYPE_CHECK='
from pathlib import Path
from vocalize.config import _validate_providers_table, ConfigError

try:
    _validate_providers_table({"elevenlabs": {"voice": 12345}}, Path("vocalize.toml"))
except ConfigError:
    pass
else:
    raise SystemExit("voice=12345 was accepted, expected ConfigError")
'

echo "=== Entry criteria ==="
check 'phase 2 (STT decoding) validated' grep -q '^validate-exit: PASS' "$ROADMAP_DIR/run-2-stt-decoding/report.md"
check 'phase 2 key artifact: q8_0 model pinned' grep -q 'large-v3-turbo-q8_0' vocalize/local/whisper_manifest.py
check 'installed Claude Code answers --help' claude --help
check 'on its own branch, not main' bash -c 'test "$(git branch --show-current)" != main'
check 'suite green at entry' .venv/bin/python -m pytest tests/ -q -x -p no:cacheprovider
check 'ruff clean at entry' .venv/bin/python -m ruff check vocalize hooks tests

echo ""
echo "=== Exit criteria ==="
check 'backends and boundaries (llm.py)' .venv/bin/python -m pytest tests/test_llm.py -q -p no:cacheprovider
check 'cleanup moved intact' .venv/bin/python -m pytest tests/test_llm.py -q -k cleanup -p no:cacheprovider
check 'verbatim keyword' .venv/bin/python -m pytest tests/test_llm.py tests/test_dictate.py -q -k verbatim -p no:cacheprovider
check 'enums and coercion' .venv/bin/python -c "$STT_ENUM_CHECK"
check '[notes] survives a config rewrite' .venv/bin/python -m pytest tests/test_wizard.py -q -k notes -p no:cacheprovider
check 'hook hardened (speak_options)' .venv/bin/python -m pytest tests/test_speak_options.py -q -k "strict or cwd" -p no:cacheprovider
check 'issue #5: provider values type-checked' .venv/bin/python -c "$PROVIDER_TYPE_CHECK"
check 'issue #5: portal reports the per-provider error' .venv/bin/python -m pytest tests/test_config.py tests/test_portal.py -q -k voice_type -p no:cacheprovider
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
