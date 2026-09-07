# Run 9: Doctor, integrate claude, Setup tab (0.13.0)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 9: Doctor, integrate claude, Setup tab (0.13.0); contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-80 | `readiness.doctor_rows()` and `vocalize doctor` per design § Readiness, doctor and the Setup tab (incl. `cli start-up`, `app bundle` with the repair line, `notes folder` size) | vocalize | T-73 | `vocalize doctor --json` lists every row incl. `cli path`, `uv`, `swiftc`, `claude`, shebang, conflicts, `cli start-up`, `app bundle`, `notes folder`; exit 1 when any row fails |
| T-81 | `vocalize integrate claude`: PATH pre-check, install the `/speak` skill (shipped in the package under `vocalize/assets/claude/speak/SKILL.md`) and the Quick Actions (installer moved into the package, baking the stable symlink not its resolved target), print the GUI-only steps | vocalize | — | on a scratch `HOME`, the skill and four `.workflow` bundles land; the baked `claude` path is the symlink; the existing Quick Action tests pass against the moved installer |
| T-82 | Portal Setup tab: the nine wizard steps of the analysis as readiness-driven rows over the existing routes; install target `app` on `/api/local/install/start` and `/status` | vocalize | T-72, T-80 | `tests/test_portal.py -k setup` green; page discipline checks (no inline script, no external URL) still pass |
| T-83 | Tests for T-80–T-82 | vocalize | T-82 | green |
| T-84 | Docs: `docs/installation.md` rewritten around `vocalize app install` + `vocalize integrate claude` + `vocalize doctor`; the six-layer table shrinks to three commands; CHANGELOG | vocalize | T-82 | present |

## Role and isolation

- **Role:** Sonnet — mechanical CLI/portal wiring over existing seams (`readiness.py`'s probe pattern, the existing install-target enum, the existing Quick Action installer); no new hardware, security model or Swift surface
- **Isolation:** branch `app`, off `main`, after run 8b (`run-8b-app-python`); works under `vocalize/readiness.py`, `vocalize/cli.py` (the `doctor`/`integrate` commands), `vocalize/portal.py`, `vocalize/assets/` (`portal.html`, `portal.js`, the new `claude/speak/SKILL.md`), and the Quick Action installer's new home inside the package; does not touch `vocalize/menubar/` or `vocalize/local/install.py`
- **Workload:** 5 source files (2 edited: `vocalize/readiness.py`, `vocalize/cli.py`; 1 edited + relocated: the Quick Action installer moving from `hooks/install_quick_action.py` into the package; 2 edited: `vocalize/portal.py`, `vocalize/assets/portal.html`/`portal.js`) + 1 new asset (`vocalize/assets/claude/speak/SKILL.md`, not a source file) + 3 test files (`tests/test_readiness.py`, `tests/test_cli.py`, `tests/test_portal.py`, plus the existing `tests/test_install_quick_action.py` re-pointed at the moved installer)

## Entry criteria

- on branch `app` (not `main`)
- run 8b (`run-8b-app-python`) validated: its `report.md` contains `validate-exit: PASS`
- run 8b's key artifact exists: `vocalize app status --json` reports `bundle`/`agent` keys (the app lifecycle T-80's `app bundle` row and T-82's install target `app` both read)
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- doctor lists every required row (verification.md § Phase 9 exit)
- integrate installs the skill and all four Quick Actions on a scratch `HOME`
- the baked `claude` path in the installed Quick Action is the symlink, not its resolved Caskroom target
- the Setup tab tests pass, and page discipline (no inline script, no external URL) still holds
- full suite green
- ruff clean
- work committed

## Not machine-checkable

- Whether the nine Setup-tab wizard steps read sensibly to a first-time user, and whether the GUI-only steps `vocalize integrate claude` prints (System Settings panes) are the right ones — a look-at-it check for the owner during the Phase 10 manual checks, not this run.
- The "existing Quick Action tests pass against the moved installer" line in T-81's acceptance criteria is proven by `tests/test_install_quick_action.py` staying green after the move — covered by "full suite green" below, not a separate manual step.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-80`–`T-84`: done | partial | skipped — reason), the security-gate result (the symlink-not-resolved-target check and the page-discipline checks named above, listed with their results), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run `run-10-release-0-13-0` reads that report as its entry criterion.
