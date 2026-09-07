# Run 4: Keychain through security (0.12.0)

Part of the 2026-09 app-roadmap plan (this plan set has no separate
choreography.md). Source plan: [plan.md](../plan.md) § Phase 4: Keychain
through security (0.12.0); contracts in [design.md](../design.md) § Keychain
through security (DEC-025), § Keychain via `security`; proof commands in
[verification.md](../verification.md) § Phase 4 exit (keychain).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-30 | **Check, 30 minutes, throwaway:** add an item with `security -i`, read it back with `find-generic-password -w` from a freshly built (different cdhash) Python and from the pipx `vocalize`; no prompt, no `-25293`. Record the outcome as DEC-035 | vocalize | — | DEC-035 Decided with the commands and their exit codes pasted; branch A (passes) or B (keep keyring, document) chosen |
| T-31 | Branch A: the `security`-backed object behind `_backend()` on Darwin (write on stdin, read `-w`, delete with read-back, `-j` validated stamp); `login` migrates a keyring-written item. Branch B: `docs/provider-credentials.md` documents the Always Allow click and the ACL cause | vocalize | T-30 | branch A: `tests/test_auth.py -k security` green with a fake `security` script logging argv and stdin (key never in argv); branch B: the doc section exists and DEC-035 says so |
| T-32 | `auth status` shows the validated stamp when present | vocalize | T-31 | test with the fake script's `-j` output |
| T-33 | Docs: `docs/provider-credentials.md` and README key section | vocalize | T-31 | commands in the docs match `vocalize auth --help` |

## Role and isolation

- **Role:** Keychain engineer — a real keychain and an interpreted 30-minute
  check gate which of two branches gets built (model tier: **Opus**, per the
  choreography's assignment for this run: the T-30 check is a judgement call
  on ambiguous exit codes, not a mechanical diff, and the backend it may gate
  touches the real keychain)
- **Isolation:** local-first — same branch (`local-first`) as Phases 1–3, no
  new worktree; runs after Phase 3 (`run-3-llm-and-enums`) on that branch
- **Workload:** 3 files carry the change (1 source: `vocalize/auth.py`, edited
  under either branch; 2 docs: `docs/provider-credentials.md`,
  `README.md`) + 1 test file (`tests/test_auth.py`); T-30 additionally writes
  the DEC-035 entry in `docs/plans/2026-09-app-roadmap/decisions.md`, which is
  plan bookkeeping, not counted as source

## Entry criteria

- on branch `local-first`, not `main`
- Run 3 (`run-3-llm-and-enums`) validated: its `report.md` contains the line
  `validate-exit: PASS`
- Run 3's key artifact exists: `vocalize/llm.py`
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it
changes to the repository root). Every line is a command's exit status; a
pre-build run must show the artifact checks failing.

- DEC-035 recorded as Decided (the commands and exit codes pasted, branch A
  or B chosen)
- the keychain backend matches the branch DEC-035 actually decided: branch A
  → `tests/test_auth.py -k security` green (key on stdin, never argv; delete
  read-back; validated stamp); branch B → `docs/provider-credentials.md`
  documents the Always Allow click
- full suite green
- ruff clean
- work committed

The verification.md row "Real read on this Mac (branch A)" is the owner's
manual check 2 below, not a script check — it needs a real pipx install and a
real Keychain, which a CI-shaped script cannot stand in for.

## Not machine-checkable

- **T-30 itself (owner present).** The 30-minute check: add an item with
  `security -i`, read it back with `find-generic-password -w` from a freshly
  built (different cdhash) Python and from the pipx `vocalize`; confirm no
  prompt and no `-25293`. The task instructions flag this run's owner need
  explicitly: T-30 needs the owner present for a possible keychain prompt.
- **Manual check 2 (branch A only, owner present).** Store a key from the
  terminal; open Claude Code desktop and run `vocalize auth status` from its
  shell: same source, no prompt.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task
(`T-nn: done | partial | skipped — reason`), the security-gate result (the
negative tests named in the acceptance criteria — key never in argv — listed
with their test ids), which branch DEC-035 landed on and why, anything
deferred, and the final line `validate-exit: PASS` copied from a real run of
the script. Run `run-5-keys-tab` reads that report as its entry criterion.
