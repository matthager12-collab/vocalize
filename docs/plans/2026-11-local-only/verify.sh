#!/usr/bin/env bash
# Exit checks for the local-only plan. Usage, from the repository root:
#
#   VOCALIZE_VERIFY_PYTHON=/path/to/venv/bin/python \
#     bash docs/plans/2026-11-local-only/verify.sh <phase 0-4>
#
# The interpreter must have the project installed with its dev extra.
# Every check prints PASS or FAIL; the script exits 1 if any check failed.
# HOME is pointed at a fresh temporary folder so nothing touches the real one.

set -u

PHASE="${1:-}"
PY="${VOCALIZE_VERIFY_PYTHON:-python3}"
BIN="$(dirname "$PY")"
FAILS=0
RUNS=0

case "$PHASE" in
  0|1|2|3|4) ;;
  *) echo "usage: verify.sh <phase 0-4>" >&2; exit 2 ;;
esac

if [ ! -f pyproject.toml ] || [ ! -d vocalize ]; then
  echo "run this from the repository root" >&2
  exit 2
fi

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/vocalize-verify.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT
mkdir -p "$SCRATCH/home"
export HOME="$SCRATCH/home"
unset XDG_CONFIG_HOME XDG_CACHE_HOME

pass() { RUNS=$((RUNS+1)); echo "PASS  $1"; }
fail() { RUNS=$((RUNS+1)); FAILS=$((FAILS+1)); echo "FAIL  $1"; }

# check NAME CMD...   passes when the command exits 0
check() {
  local name="$1"; shift
  if "$@" >"$SCRATCH/out.txt" 2>&1; then pass "$name"; else fail "$name"; tail -5 "$SCRATCH/out.txt" | sed 's/^/      /'; fi
}

# check_not NAME CMD...   passes when the command exits non-zero
check_not() {
  local name="$1"; shift
  if "$@" >"$SCRATCH/out.txt" 2>&1; then fail "$name"; head -5 "$SCRATCH/out.txt" | sed 's/^/      /'; else pass "$name"; fi
}

help_lists() { "$BIN/vocalize" --help 2>/dev/null | grep -Eq "^  $1( |\$)"; }
tree_mentions() { grep -rIlEi "$1" vocalize hooks pyproject.toml; }
suite() { "$PY" -m pytest -q --no-header -p no:cacheprovider -o addopts=""; }
lint() { "$BIN/ruff" check .; }

phase0() {
  check "90b5dbc is an ancestor of HEAD" git merge-base --is-ancestor 90b5dbc HEAD
  check "origin/main is an ancestor of HEAD" git merge-base --is-ancestor origin/main HEAD
  check "lint passes" lint
  check "suite passes" suite
}

phase1() {
  check_not "no meeting module" test -e vocalize/meetings.py
  check_not "no meetings command" help_lists meetings
  check_not "no summarize-text command" help_lists summarize-text
  check "transcribe command exists" help_lists transcribe
  check_not "no URL gate hook" test -e hooks/speak_url_gate.py
  check_not "no notes templates" test -d vocalize/assets/notes
  check_not "package, hooks and metadata do not mention meetings or zoom" tree_mentions 'meeting|zoom'
  check_not "no summarize function left in the package" grep -rqE 'def summarize|summarize-text' vocalize hooks
  check "local-only tests exist" test -f tests/test_local_only.py
  check "local-only tests pass" "$PY" -m pytest -q --no-header -p no:cacheprovider -o addopts="" tests/test_local_only.py
  check "settings keep the notes.summarizer=off line" sh -c "'$BIN/vocalize' settings | grep -qx 'notes.summarizer=off'"
  check "lint passes" lint
  check "suite passes" suite
}

phase2() {
  check "settings registry exists" test -f vocalize/settings_registry.py
  check "schema route is declared" grep -q '"/api/schema"' vocalize/portal.py
  check "single save route is declared" grep -q '"/api/settings"' vocalize/portal.py
  check_not "per-table save routes are gone" grep -Eq '"/api/(stt|chain|provider/[a-z]+)"' vocalize/portal.py
  check "parity tests exist" test -f tests/test_settings_parity.py
  check "parity tests pass" "$PY" -m pytest -q --no-header -p no:cacheprovider -o addopts="" tests/test_settings_parity.py
  check "lint passes" lint
  check "suite passes" suite
}

phase3() {
  local here="docs/plans/2026-11-local-only"
  check "measurements are recorded" test -s "$here/measurements.md"
  check "Studio configuration exists" test -s "$here/studio-config.toml"
  check "Studio configuration loads without warnings" sh -c "mkdir -p '$HOME/.config/vocalize' && cp '$here/studio-config.toml' '$HOME/.config/vocalize/config.toml' && '$BIN/vocalize' settings 2>'$SCRATCH/warn.txt' >/dev/null && test ! -s '$SCRATCH/warn.txt'"
  check "machine profiles are tested" test -f tests/test_profiles.py
  check "lint passes" lint
  check "suite passes" suite
}

phase4() {
  check "version is 1.0.0" grep -q '^__version__ = "1.0.0"$' vocalize/__init__.py
  check "changelog has a 1.0.0 section" grep -q '^## 1\.0\.0 - ' CHANGELOG.md
  check_not "README names no cloud provider or key" grep -Eqi 'elevenlabs|openai|polly|api key' README.md
  check_not "metadata names no cloud dependency" grep -Eqi 'elevenlabs|keyring|boto3|dotenv' pyproject.toml
  check "wheel and sdist build" "$PY" -m build --outdir "$SCRATCH/dist"
  check "lint passes" lint
  check "suite passes" suite
}

"phase$PHASE"

echo "phase $PHASE: $((RUNS-FAILS)) of $RUNS checks passed"
[ "$FAILS" -eq 0 ]
