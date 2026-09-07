# Run 2: STT decoding (0.12.0)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 2; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-10 | `whisper_worker.py` constructs the model with the beam-search strategy; `[stt] beam_size` (1–8, default 5, 1 = greedy) is the escape hatch, validated like every `[stt]` key and passed through `worker_argv`; time the 30 s spike clip cold and warm at beam 1 and beam 5 and record the numbers in `spike-notes.md` § Beam; if beam 5 more than doubles the time, re-derive `_TRANSCRIBE_TIMEOUT` with headroom in the same task | vocalize | — | the stub-Model test asserts the strategy kwargs and that `beam_size=1` yields greedy; four timings in `spike-notes.md`; `vocalize listen --wav <spike clip>` no longer merges "to get" in the owner's clip (manual check 1) |
| T-11 | `whisper_manifest.py` gains `large-v3-turbo-q8_0` (874 MB) at the same pinned revision, size and sha256 from a verified download; `[stt] model` allowlist and docs table updated | vocalize | — | `pytest tests/test_whisper_manifest.py -q` green; the sha256 in the manifest equals `shasum -a 256` of the downloaded file (recorded in `spike-notes.md`) |
| T-12 | CoreML measured, not built: run `ONNX_PROVIDER=CoreMLExecutionProvider vocalize speak "<fixed sentence>"` and the CPU default three times each, cold and warm, with peak RSS of the worker for each path; record the numbers in `spike-notes.md`; correct the Kokoro RAM figure in the provider docstring and docs to the measured value; add a `[providers.kokoro] provider` knob **only** if CoreML wins by more than 20 % | vocalize | — | `spike-notes.md` § CoreML holds six timings, two RSS figures and a verdict line; the docs figure matches; no code change unless the verdict is "wins" |

## Role and isolation

- **Role:** Python config and chain — Sonnet (T-11's hash pin is double-checked against a real download; no step in this run needs a heavier model)
- **Branch:** `local-first`, after Phase 1 is merged into it
- **Isolation:** `vocalize/local/whisper_worker.py`, `vocalize/local/whisper_manifest.py`, `vocalize/providers/kokoro.py` (docstring, and the provider knob only if T-12's verdict is "wins"), `vocalize/dictate.py` (only if T-10's timing forces `_TRANSCRIBE_TIMEOUT` up)
- **Workload:** 4 source files (2 always edited: `whisper_worker.py`, `whisper_manifest.py`; 2 conditionally edited: `kokoro.py` docstring/knob, `dictate.py` timeout) + 3 test files (`test_whisper_worker.py`, `test_config.py`, `test_whisper_manifest.py`); `spike-notes.md` is the run's other deliverable, not code

## Entry criteria

- Phase 1 merged to `local-first`
- on its own branch (not `main`)
- run 1 (`run-1-local-first-defaults`) validated: its `report.md` carries `validate-exit: PASS`
- run 1's key artifact exists: `vocalize/config.py` carries `DEFAULT_CHAIN = ("kokoro", "say")`
- suite green at entry
- ruff clean

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- beam search on, with an escape hatch (stub-Model test + `[stt] beam_size` validation)
- beam cost measured: `spike-notes.md` § Beam carries the four cold/warm × beam 1/5 timings
- turbo q8_0 row pinned in the manifest (test)
- turbo q8_0 documented (`docs/dictation.md`)
- CoreML measured: `spike-notes.md` § CoreML carries the eight cold/warm/RSS figures and a verdict line
- full suite green
- ruff clean
- work committed

## Not machine-checkable (owner present)

- T-10's beam timings and T-12's CoreML timings are real clock/RSS measurements taken on the owner's Mac — no click needed, but a script cannot generate them.
- T-10 manual check 1: `vocalize listen --wav <spike clip>` no longer merges "to get" in the owner's own clip.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result, anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 3 (`run-3-llm-and-enums`) reads that report as its entry criterion.
