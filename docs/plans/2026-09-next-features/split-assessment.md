# Split assessment: local dictation, readiness status, and the config portal

Date 2026-09-02. Plan: [plan.md](./plan.md). Execution: agent runs via `implement-spec`, one task at a time, on the reference Mac (M3, 8 GB) in the `vocalize` repository; the owner is present only for the live checks and the publishes.

## Factor values (calibration of 2026-09-02)

| Factor | Comfortable | Consider splitting | This plan | Verdict |
|---|---|---|---|---|
| Total tasks | 1–25 | 25+ | 39 (5 done in the spike, 34 remaining) | over |
| Phases with sequential dependencies | 1–3 | 4+ | 8 (Phase 1 can run beside Phase 2; the other seven are a chain) | over |
| Distinct roles needed | 1–4 | 5+ | 8 (spike runner done; runtime plumbing, recorder engineer, dictation core, readiness, portal server, portal page, reviewers) | over |
| Working directories / worktrees | 1 | 2+ | 3 (main checkout on `next-features`, a worktree for `status`, then branch `config-portal`) | over |
| Cross-cutting handoffs or gates | 0–2 | 3+ | 6 (two adversarial reviews, two owner-present live checks, two publishes) | over |
| Tasks needing external validation | 1–8 | 8+ | 9 (T-23 real install, T-31/T-32 real bundle and grant, T-52, T-53, T-71, T-82, plus manual checks 0 and 5) | over |

## Qualitative signals

- **Context pressure — decisive.** Three languages (Python, Swift, JavaScript), roughly 2,500 lines of new code across 14 unique source files, about 150 new tests, and two release cycles. One executor tracking all of that would be summarizing its own earlier work by Phase 4.
- **Blast radius.** The recorder (Swift, TCC, codesign) and the interrupt record (edits to `audio.py`, `chain.py`, `cli.py`) are the two places a failure would poison everything after them. Each gets its own run so a red exit stops the chain there.
- **Natural checkpoints.** Every phase exit in [verification.md](./verification.md) is already a set of commands with exit codes, and the two releases are hard stops that need the owner. Splitting on those boundaries adds no new ceremony.

## Workload by files, not statements

| Run | Unique files | Weighting |
|---|---|---|
| 1 status | 3 | threading seam and blocked-probe fixture ×2 |
| 2 stt-runtime | 6 source + 4 test | `install.py` generalization ×3 (two manifests must keep passing) |
| 3 recorder | 1 Swift + plist + 2 Python + 2 test | unfamiliar API, hardware-adjacent ×3 |
| 4 dictation | 4 source + bundle + 5 test | state machine with three fakes ×3 |
| 5 resume | 5 core edits + 4 test | shared playback path, every existing test must stay green ×3 |
| 6 release 0.10.0 | docs + review | owner-gated |
| 7 portal-read | 1 new (~500 lines) + 1 test | auth bootstrap ×3 |
| 8 portal-write | 3 edits + test | mutating surface ×2 |
| 9 portal-page | 2 new assets + test | template-like ×1 |
| 10 release 0.11.0 | docs + review | owner-gated |

No single file approaches 1,000 lines (`cli.py` is 859 today and grows by three commands), so no file needs its own executor.

## Recommendation

**Split into ten runs** along the phase boundaries, with Phase 4 halved (state machine vs. the core-module interrupt record) and Phase 6 halved (read-only auth/state vs. the mutating surface), exactly as [plan.md](./plan.md) § Suggested run boundaries anticipated. Run 1 runs in parallel with run 2 in its own worktree; everything else is sequential. Sequence and handoffs are in [choreography.md](./choreography.md); each run's tasks, entry and exit criteria are in `run-N-*/project-plan.md` with a `validate-exit.sh` that was executed pre-build (artifact checks fail, regression checks pass — see the log summary in the choreography).

## Open questions

1. **Owner windows.** Runs 6, 9 (T-71) and 10 need the owner at the keyboard. Nothing blocks the agents until run 6, so the first window can be booked once run 5 is green.
2. **Merge runs 7 and 8** if `portal.py` stays well under 500 lines after run 7; the split exists for the review boundary, not for size.
3. **Model tiers** are named per run in the choreography (Sonnet for mechanical, Opus for judgement) per the machine-wide rule; the executor may step a tier up on a failed retry and must say so in `report.md`.
