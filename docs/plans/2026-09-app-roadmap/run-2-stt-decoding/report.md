# Report: run 2, STT decoding

Date 2026-09-07. Branch `local-first` (worktree `.claude/worktrees/local-first`). Full account in [task-report.md](./task-report.md); measurements in [../spike-notes.md](../spike-notes.md).

- T-10: done — beam search with five beams by default, `[stt] beam_size` 1–8 as the escape hatch, timings recorded (beam 5 costs about 18 % on a 43 s clip; the timeout is unchanged).
- T-11: done — `large-v3-turbo-q8_0` pinned from a completed download (874,188,075 bytes); manifest tests, the portal page's model list, the status fixture and the docs updated.
- T-13: done — default model `large-v3-turbo-q5_0` (owner's call after manual check 1), with the upgrade note in the CHANGELOG.
- T-12: done — CoreML measured on four runs per path and rejected (75 % slower, 600 MB more RAM); no knob added; the Kokoro RAM figure corrected to 760 MB in the docstring and README.

Security gate: no security-negative tests belong to this phase. The one new subprocess argument, `--beam-size`, is an integer validated in config (1–8) and again by the worker's parser (`choices=range(1, 9)`); a value off the range never reaches the model.

Deferred: nothing. Manual check 1 was run the same day: on the owner's own voice both beam 1 and beam 5 produced "themerge" for "the merge", so beam search did not close issue #4. The decoding change stays (it costs 18 % and never hurt a transcript). The same take through `large-v3-turbo-q5_0` wrote "the merge" correctly at both beam settings and no slower, so the answer to #4 on the owner's voice is the model tier (spike-notes § Beam); the default model for 16 GB machines is now a decision for the owner, and #4 stays open until it is taken. The turbo run also showed the worker joining segments without a space ("working.I want"); with the owner's approval the worker now strips each segment and joins with one space, with a test, and the same take reads "working. I want" through turbo.

Suite: 1788 passed, 3 skipped. Ruff clean.

validate-exit: PASS
