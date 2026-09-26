# Split assessment: intended mode for dictation

Split-plan, 2026-09-26, against [plan.md](plan.md).

## Factors

| Factor | Value | Comfortable | Consider splitting | Reading |
|---|---|---|---|---|
| Total tasks | 22 (T-01 done separately; 21 left) | 1–25 | 25+ | comfortable |
| Phases with sequential dependencies | 6 (0 to 5) | 1–3 | 4+ | **split** |
| Distinct roles | 4 (builder, eval runner, reviewer, the owner) | 1–4 | 5+ | comfortable |
| Working directories / worktrees | 1 repository, a worktree per phase | 1 | 2+ | borderline |
| Cross-cutting handoffs or gates | 4 eval gates (cleanup, leak, timing and memory, all on one commit) plus the owner check | 0–2 | 3+ | **split** |
| Tasks needing external validation | 5 (T-12, T-22, T-36, T-51, T-52) | 1–8 | 8+ | comfortable |

## Workload by unique file

- **`dictate.py` (1,846 lines)** is touched by T-02, T-21, T-34, T-40 and T-41. It is over the 1,000-line mark, so each run that touches it gets one builder for it. The runs are sequenced so no two edit it at once.
- **New files with their own test strategy:** `warm_protocol.py`, `warm.py`, the two `--serve` loops (each needs a fake clock, a stub model and a socket fixture), `clipboard.js`, the Quick Action bundle, and three eval tests that need the real models.
- **Template-like, cheap:** the two config keys with portal and docs, the CHANGELOG.

## Qualitative

- **Context pressure.** The warm-server phase alone is four new units plus wiring into `dictate.py`. One executor holding every phase would carry the protocol, both servers, the client, the wiring, the undo and three evals. This is the real constraint.
- **Blast radius.** Phases 1 and 2 are useful on their own: better cleanup, and jargon. A failure in the warm servers must not hold them back. The warm servers split again, so a server bug is found before `dictate.py` is touched.
- **Natural checkpoints.** Every phase ends with a green suite and, where it has one, a green eval gate, followed by a merge to `main`. Those are the cuts.

## Recommendation

**Split into six runs, on the phase boundaries, with Phase 3 cut in two** (servers, then wiring). They run in order, one at a time, because four of them edit `dictate.py`. See [choreography.md](choreography.md).

## Open questions

None. The undo (run 5) depends only on run 1's guard. It could run earlier, but it edits `dictate.py`'s delivery path, which runs 1 and 4 also edit, so it waits rather than risk a three-way conflict.

## Exit scripts, proven before the build

All six `validate-exit.sh` scripts were run on 2026-09-26 against the plan branch, before any build. Each exited 1. Every check for a not-yet-built artifact failed with exit 1. The only passes were regression checks: `ruff check .` (6), the whole unit suite (6) and the plan lint (run 6).

The first attempt caught two bugs in the scripts, both fixed before this run:
- `timeout` cannot call a shell function, so the pytest helpers returned 127.
- A 120 s default timeout would have killed the 160 s suite and the evals.

It also caught two vacuous checks. Both are now pinned: a docs grep that the old text already satisfied, and an orphan check with no existence precondition.
