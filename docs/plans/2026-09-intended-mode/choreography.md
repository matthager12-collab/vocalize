# Choreography: intended mode for dictation

Six runs, in order. Each is executed with implement-spec, one task at a time. Each ends when its `validate-exit.sh` exits 0 on the run's branch, the branch's CI is green, and it is squash-merged to `main`. The next run cuts its branch from that `main`.

| Run | Delivers | Needs from before | Touches `dictate.py` |
|---|---|---|---|
| [run-1-meaning](run-1-meaning/project-plan.md) | Nonce lock on delivery, intended prompt, guard, cleanup eval | The control-token and tokenizer fix (PR #16) on `main` | yes (T-02) |
| [run-2-jargon](run-2-jargon/project-plan.md) | `[stt] vocabulary`, whisper initial prompt, leak eval | run 1 on `main` | yes (T-21, `worker_argv`) |
| [run-3-warm-servers](run-3-warm-servers/project-plan.md) | Protocol, both `--serve` loops, the client, not wired in | run 2 on `main` (the whisper server needs T-21's prompt) | no |
| [run-4-warm-wiring](run-4-warm-wiring/project-plan.md) | Warm servers wired into dictation and `listen`, `warm_minutes`, timing and memory eval | run 3 on `main` | yes (T-34) |
| [run-5-undo](run-5-undo/project-plan.md) | Two-type clipboard, `dictate --swap`, the Quick Action, privacy docs | run 4 on `main` | yes (T-40, T-41) |
| [run-6-release-ready](run-6-release-ready/project-plan.md) | CHANGELOG and docs, the owner's config block, owner check, every eval on one commit | run 5 on `main` | no |

## Handoff protocol

1. **Start.** Cut `intended-run-N` from `main` in its own worktree, then run the entry block (`bash docs/plans/2026-09-intended-mode/run-N-*/validate-exit.sh`). Entry checks must pass; exit checks are expected to fail at this point.
2. **Build.** implement-spec, task by task. Builders get the run's project-plan.md, the relevant decisions and the files. Routing follows `~/workspace/infra/agent-team/routing.md`. Claude drives, briefs, reads every output and does the git. Every brief sets `HOME` and `XDG_CONFIG_HOME` to a scratch directory.
3. **Review.** A reviewer from another model family reads the run's diff, framed as a defensive coverage review. Findings are checked against the files before acting.
4. **Exit.** `validate-exit.sh` exits 0 in the run's worktree, with its output kept. The eval gates run on the reference Mac.
5. **Merge.** Push, wait for CI green, and squash-merge (the Published code class: suite output kept, scrub of the diff clean). A merge to `main` releases nothing; the PyPI release stays the owner's.
6. **Record.** Append the run's outcome to the plan branch's spike-notes.md, if measurements changed, and one line to the Load Line log.

## Stop rules

- An eval gate fails, or a DEC-047 threshold is missed: stop the run and bring the numbers to the owner. Do not loosen a threshold in the same run.
- A reviewer finding rated High that cannot be fixed inside the run: stop, and open a decision entry.
- Run 6's T-51 and T-52, and the release, are the owner's.
