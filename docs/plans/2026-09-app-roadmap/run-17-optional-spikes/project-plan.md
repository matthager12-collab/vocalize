# Run 17: Optional spikes on no release path

Part of choreography for [plan.md](../plan.md). Source plan: [plan.md](../plan.md) § Phase 17; contracts in [design.md](../design.md); this phase has no exit row in [verification.md](../verification.md) — it gates no release.

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-160 | Foundation Models cleanup helper, 4–6 h, once Apple Intelligence is on: a swiftc binary calling `SystemLanguageModel` with plain string responses; measure latency and the 4096-token limit | scratch | — | numbers and a keep-or-drop verdict |
| T-161 | SpeechAnalyzer in a scratch bundle, 4–8 h: jargon accuracy on the owner's clip versus whisper, the Speech Recognition grant, file input | scratch | — | numbers and a verdict |
| T-162 | Voice Memos embedded transcript, 2 h: read the transcript atom from a macOS 26 memo; if readable, `notes.embedded_transcript()` gains a body | scratch → vocalize | — | verdict; a test with a fixture m4a if shipped |

## Role and isolation

- **Role:** Opus, owner present — each spike needs a human voice, a click, or a judgement call the model cannot make alone; every spike is optional and throwaway.
- **Isolation:** scratch, outside the repo. T-160 and T-161 never touch `vocalize/`; both are one-off swiftc binaries or bundles built and run in a scratch directory, never committed. T-162 starts in scratch and only crosses into `vocalize/` if the transcript atom turns out to be readable — and even then, landing that change in the repo is a separate, later piece of work, not part of this run's exit.
- **Workload:** 0 source files, 0 test files from the task table itself (all three tasks build throwaway scratch binaries/bundles, not vocalize source or tests); the one file this run writes to is `spike-notes.md`, which is not a source file.

## Entry criteria

- suite green at entry
- ruff clean

There is no previous run: Phase 17 has no dependency edge from any other phase (see plan.md § Dependencies) and no run precedes it in the choreography. This run starts from `main` after 0.11.0 — the state the whole app-roadmap plan starts from — not from any run in this plan.

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- Foundation Models spike recorded, if run (spike-notes.md § Foundation Models)
- SpeechAnalyzer spike recorded, if run (spike-notes.md § SpeechAnalyzer)
- Voice Memos spike recorded, if run (spike-notes.md § Voice Memos)
- nothing under `vocalize/` changed

This phase has no release-path exit, no suite/ruff/commit gate of its own, and no DEC row — none of the three spikes gates a decision entry in decisions.md. A spike the owner skips simply has no section; that is a valid outcome for an optional, throwaway phase.

## Not machine-checkable

- Every spike itself: each needs the owner's voice, the owner's jargon clip, or the owner's macOS 26 Voice Memos library. The script only checks that a spike the owner ran left a recorded verdict — it cannot run or judge the spike.
- Whether T-162 landed a change in `vocalize/notes.py`: if the transcript atom is readable and the owner chooses to ship `embedded_transcript()`, that lands as its own out-of-band change on its own branch — not as part of this run's exit, which requires `vocalize/` to stay untouched.

## Handoff

None. This is the last run in the choreography — no run reads this one's report.md as an entry criterion.
