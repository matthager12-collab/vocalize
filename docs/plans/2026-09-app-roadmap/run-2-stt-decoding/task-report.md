# Task report: run 2, STT decoding

Date 2026-09-07. Branch `local-first`, worktree `.claude/worktrees/local-first`. Executed with implement-spec against [project-plan.md](./project-plan.md); exit gate [validate-exit.sh](./validate-exit.sh); result in [report.md](./report.md); numbers in [../spike-notes.md](../spike-notes.md).

## Changelog

- `whisper_worker.py` takes `--beam-size N` (1–8, default 5): 1 keeps the greedy decoder, otherwise `params_sampling_strategy=1` with `beam_search={"beam_size": N, "patience": -1.0}`.
- `[stt] beam_size` (1–8, default 5) validated like `max_seconds`, passed through `worker_argv`, printed by `settings`, documented in `docs/dictation.md`.
- `whisper_manifest.py` gains `large-v3-turbo-q8_0` (874,188,075 bytes, sha256 from a completed download); the manifest tests count four models and pin the hash; the portal page's model list and the `local status` test fixture carry the fourth name; `docs/dictation.md` lists it.
- CoreML measured and rejected (75 % slower, 600 MB more); no knob. The Kokoro RAM figure in the provider docstring and README corrected to the measured 760 MB.
- `whisper_worker._join_segments`: segments stripped and joined with one space, so the turbo models (no leading space per segment) no longer glue sentences together; approved by the owner as an addition after the manual check. Test with a stub that emits no leading spaces.
- T-13 (added by the owner after manual check 1): the default dictation model is `large-v3-turbo-q5_0` in config, manifest, portal page and docs, with a CHANGELOG upgrade note for users whose `[stt]` sets no model.
- CHANGELOG: Changed (beam search, segment join, default model) and Added (q8_0) under Unreleased.

## Skipped

Nothing skipped. The transcribe timeout stays at 300 s because beam 5 cost 18 %, not the 2× that would have moved it. One claim withdrawn: beam search was expected to close issue #4 and, on the owner's own voice, it did not (both decoders wrote "themerge"); #4 stays open.

## Learnings

- **pywhispercpp's `beam_search` dict needs both keys.** `{"beam_size": N}` alone raises `KeyError: 'patience'` inside the binding before the model loads; the worker reports it as a one-line error that `vocalize listen` shows as "could not read the recording". Stub-model tests cannot catch this; running the worker's argv by hand did.
- **A new manifest row touches three lists, not one:** `whisper_manifest.FILES`, `portal.js`'s `STT_MODELS`, and the `local status` test fixture's payload table. The page and the fixture each have a test that drifts loudly, which is how they were found.
- **CoreML for Kokoro loses on the M4**, not merely a wash: 5 s against 3 s, and 1.4 GB against 0.76 GB.
- **Exit-gate greps count lines after a heading.** Prose between a heading and its figures pushes the figures past the window; put the numbers first.
- **Timing methodology.** Cold is run 1 of a fresh process series; warm is the median of the next runs. The spike's original clips were not kept; a `say` render of a jargon paragraph stands in for speed, never for accuracy.

## Alternatives

- **A `--greedy` flag instead of a numeric `beam_size`.** Rejected: a number is the same escape hatch and lets a slow machine pick 2 or 3.
- **Catch the binding's KeyError and retry without beam search.** Rejected: passing the documented `patience` default is one field and needs no guard.
- **Pin the q8_0 hash from the HTTP `x-linked-etag` header.** Rejected: the manifest rule is a hash from the completed file.

## Specification, as built

Identical to [project-plan.md](./project-plan.md), with two additions to T-11 found by the suite (the portal page's `STT_MODELS` list and the test fixture's payload table) and one owner-approved addition after manual check 1: the segment-join fix in the worker.
