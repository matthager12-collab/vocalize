# Split assessment: local-first defaults, the menu-bar app, and recorded notes

Date 2026-09-06. Plan: [plan.md](./plan.md). Execution: agent runs via `implement-spec`, one task at a time, on the owner's Mac mini (M4, 16 GB, macOS 26.5.1) in the `vocalize` repository; the owner is present only for spikes that need a voice or a click, the manual checks, the squash-merges and the publishes. The owner asked for the smallest chunks possible so usage can be spread out.

## Factor values (calibration of 2026-09-02, unchanged)

| Factor | Comfortable | Consider splitting | This plan | Verdict |
|---|---|---|---|---|
| Total tasks | 1–25 | 25+ | 75 | over |
| Phases with sequential dependencies | 1–3 | 4+ | 17 in one chain (Phase 17 hangs off nothing) | over |
| Distinct roles needed | 1–4 | 5+ | 9 (config and chain, keychain, Swift app, app lifecycle, readiness and portal, dictation core, runtime plumbing, notes, reviewers) | over |
| Working directories / worktrees | 1 | 2+ | 4 branches in sequence off `main` plus scratch environments for the spikes; one checkout at a time | over |
| Cross-cutting handoffs or gates | 0–2 | 3+ | 15 (four releases, five adversarial reviews, six owner-present spikes or checks) | over |
| Tasks needing external validation | 1–8 | 8+ | 17 (T-10, T-11, T-12, T-30, T-52, T-60, the 8b real build, T-92, T-100, T-111, T-120, T-121, T-130, T-146, T-152, T-160 to T-162) | over |

## Qualitative signals

- **Context pressure, decisive.** Four languages (Python, Swift, JavaScript, shell), roughly 4,000 lines of new code across some 30 source files, about 300 new tests, and four release cycles. One executor would be summarising its own earlier work before the app phase began.
- **Blast radius.** Three places where a failure poisons everything after: `llm.py` (every cloud call and every prompt route through it), the Swift app (one shot, because every later edit is an Accessibility re-grant), and the cue trim in `dictate.py` (the shared dictation path). Each gets its own run so a red exit stops the chain there.
- **Natural checkpoints.** Every phase exit in [verification.md](./verification.md) is a set of commands with exit codes, and the four releases are hard stops that need the owner. Splitting on those boundaries adds no ceremony.

## Workload by files, not statements

| Run | Unique files | Weighting |
|---|---|---|
| 1 defaults | 3 source + 3 test + docs | literal flip, ×1 |
| 2 decoding | 2 source + 2 test | hash pin from a real download, ×2 |
| 3 llm.py and enums | 1 new module + 5 edited + 4 test | the security seam, ×3 |
| 4 keychain | 1 source + 1 test | a real keychain and an interpreted check, ×3 |
| 5 keys tab | 3 source + `portal.js` + 3 test | mutating routes, ×2 |
| 6 release 0.12.0 | docs + review | owner-gated |
| 7 spike and builder | 1 source + 2 test | golden test guarding a re-grant, ×3 |
| 8a app, Swift | 1 Swift + plist | unfamiliar API, one shot, ×3 |
| 8b app, Python | 1 new module + 5 edited + 3 test | launchd and TCC side effects, ×2 |
| 9 doctor, integrate, setup | 3 edited + assets + 3 test | mechanical, ×1 |
| 10 release 0.13.0 | docs + review | owner-gated |
| 11 cue, hold, paste | 2 edited + 2 test | timing on real audio, ×3 |
| 12 release 0.13.1 | docs + review | owner-gated |
| 13 spikes | scratch + decisions | interpreted, owner voice |
| 14 local model | 2 new + 4 edited + 4 test | token-id prompt, manifest hardening, ×3 |
| 15 notes | 1 new module + 4 assets + 2 edited + 3 test | untrusted-input paths, ×2 |
| 16 release 0.14.0 | docs + review | owner-gated |
| 17 optional spikes | scratch | owner present |

No single file approaches 1,000 lines after the split: `cli.py` (1,702 today) grows by four command groups spread over five runs and shrinks by the cleanup code that moves to `llm.py`; `dictate.py` (1,370) loses the cleanup block in run 3 before gaining the trim in run 11.

## Recommendation

**Split into eighteen runs**, one per phase with Phase 8 halved (Swift, then Python), exactly as [plan.md](./plan.md) § Suggested run boundaries proposes. Every run is sequential; there is no parallel branch this time, because each release is a hard stop and the four branches fork from `main` after the previous publish. No run exceeds about 20 hours; runs 1, 2, 4, 6, 10, 12, 13 and 16 are under 6 hours each, so a session with little usage left can still finish one. Sequence, tiers and handoffs are in [choreography.md](./choreography.md); each run's tasks, entry and exit criteria are in `run-N-*/project-plan.md` with a `validate-exit.sh` executed pre-build (artifact checks fail, regression checks pass; see the log summary in the choreography).

## Open questions

1. **Owner windows.** Runs 2 (two timing measurements), 4 (a possible keychain prompt), 6, 7 (three presses), 8b (the Accessibility click), 10, 11 (a Bluetooth input), 12, 13 (the jargon clip), 14 (a 3 GB download), 15 (a 60-minute recording), 16 and 17 need the owner at some point. Runs 1, 3, 5, 8a and 9 do not.
2. **Merging small runs.** Runs 1 and 2 are each under four hours and may be executed back to back in one session without changing any document; the same holds for runs 12 and 13.
3. **Model tiers** are named per run in the choreography per the machine-wide rule (Sonnet for mechanical work, Opus only where judgement matters); an executor may step a tier up after a failed retry and must say so in `report.md`.
