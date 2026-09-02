# Run 9: Portal page (HTML/JS) and the owner's UX pass

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 7; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-70 | `vocalize/assets/portal.html` + `portal.js` (no inline script, no external resources, system fonts, inline SVG): tabs Chain (up/down), Providers (voice dropdown + preview + speed + budget), Keys (masked; `autocomplete="off"`), Usage, Local (install with progress; `[stt]` model, language and input device from `--list-devices`); persistent readiness sidebar from `/api/state` | vocalize | Phase 6 | served HTML contains no `<script>` body and no external URL; every tab's requests carry the header |
| T-71 | UX pass with the owner (one iteration budgeted) | vocalize | T-70 | owner's changes applied |

## Role and isolation

- **Role:** Portal page — mechanical from the route contract (Sonnet); T-71 with the owner
- **Isolation:** branch `config-portal`, after run 8; new directory `vocalize/assets/`
- **Workload:** portal.html + portal.js (new, no framework, inline SVG, system fonts) + a served-asset test; browser verification is manual

## Entry criteria

- on branch config-portal
- run 8 validated
- portal command present
- suite green at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- assets exist
- no inline script in the page
- no external URL in page or script
- page served with headers (tests)
- assets ship in the wheel
- full suite green
- ruff clean
- work committed

## Not machine-checkable (owner present)

- T-71 UX pass with the owner: every tab loads with a hanging provider faked via config; preview plays under the shipped headers; the Local tab sets the input device (verification Manual 6).

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 10 (`run-10-release-0-11-0`) reads that report as its entry criterion.
