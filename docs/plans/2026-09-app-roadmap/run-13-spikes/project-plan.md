# Run 13: Spikes that gate 0.14.0

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 13; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-120 | **Parakeet spike, 2 hours:** `sherpa-onnx==1.13.7` with the int8 Parakeet archive versus whisper `small.en` and turbo q8_0 on the owner's 30 s jargon clip and one 20-minute recording: jargon misses, stop-to-text time, peak RSS. Go only if fewer misses, no slower than `small.en`, RSS under 1.5 GB. Record DEC-034 | scratch | — | DEC-034 Decided with the numbers |
| T-121 | **mlx-lm spike, 1 hour:** in a throwaway env confirm `load()`/`generate()` signatures, `tokenizer_config` passthrough, `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` keep it offline, the Qwen3.5-4B 4-bit repo's `model_type` loads text-only, the exact think-off ChatML string, one safetensors or an index plus shards, and the latency a real press pays: five repeated **cold** one-shot runs (fresh `uv run` each, page cache warm) on a 30 s cleanup; also peak combined RSS with a Kokoro read playing, Claude Code and a browser open; record in `spike-notes.md` § LLM | scratch | — | every item answered; if the median cold one-shot exceeds 4 s the plan notes a resident session for dictation cleanup in T-133 |
| T-122 | **Only if DEC-034 is go:** `parakeet_manifest.py` (archive URL, size, sha256, a tar member allowlist and size cap in `install.py`), `parakeet_worker.py` with the `--segments` contract and the stub-Model test, `[stt] engine = whisper\|parakeet`, `dictate.worker_argv` dispatch, `local install --stt --engine parakeet`, CC-BY attribution in docs | vocalize | T-120 | `tests/test_parakeet_*.py` green; a tar member outside the allowlist or over the cap is refused; `vocalize listen --wav clip.wav` works with either engine |

## Role and isolation

- **Model tier:** Opus — T-120's and T-121's numbers are interpreted, not mechanically read off a log: the go/no-go call on DEC-034 (fewer jargon misses, no slower than `small.en`, RSS under 1.5 GB) and the resident-session judgement call in T-121 both turn on a human reading of noisy timing data.
- **Branch:** `notes`, off `main`, forked after 0.13.1 ships (run 12).
- **Isolation:** T-120 and T-121 run in throwaway `uv` environments outside the repo — they touch no tracked source, only `decisions.md` (DEC-034) and `spike-notes.md` § LLM. T-122 is conditional on a go decision and is the only task that edits `vocalize/` on this branch at this point in the chain; per plan.md § Roles this branch also carries Phase 14 (`local/`, `llm.py`), so T-122 and Phase 14's manifest/worker work share the same `vocalize/local/` directory but not the same files.
- **Workload:** T-120/T-121 touch 0 source files and 0 test files (scratch spikes; the deliverables are `decisions.md` and `spike-notes.md`, plan bookkeeping, not code). T-122, only if DEC-034 is go: 4 source files (2 new: `vocalize/local/parakeet_manifest.py`, `vocalize/local/parakeet_worker.py`; 2 edited: `vocalize/local/install.py` for the tar allowlist and size cap, `vocalize/cli.py` for `local install --stt --engine parakeet`) plus `vocalize/config.py` and `vocalize/dictate.py` edited for the `[stt] engine` key and the `worker_argv` dispatch (6 source files total) + 3 test files (2 new: `tests/test_parakeet_manifest.py`, `tests/test_parakeet_worker.py`; 1 edited: `tests/test_local_install.py` for the tar-allowlist refusal) + one doc file for CC-BY attribution (not source, not test).

## Entry criteria

- on branch `notes`, not `main`
- run 12 (`run-12-release-0-13-1`) reported `validate-exit: PASS`
- run 12's key artifact exists (the 0.13.1 wheel in `dist/`)
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- Parakeet decided (DEC-034 status `Decided` in `decisions.md`)
- mlx-lm spike answered (7 labelled lines under `spike-notes.md` § LLM: `signatures`, `offline`, `model_type`, `template`, `shards`, `cold`, `warm`)
- Parakeet engine tests green, go only (`pytest -k parakeet`; stays failing with "no tests ran" if DEC-034 is no-go, since no `test_parakeet_*.py` is ever written on that branch)
- full suite green
- ruff clean
- work committed

## Not machine-checkable

- **T-120, the Parakeet spike itself (owner present).** The owner reads the 30 s jargon clip and a 20-minute recording aloud through both engines; jargon misses, stop-to-text time and peak RSS are counted by ear and by eye, not by a script. This is verification.md manual check 12. The automated DEC-034 check above only confirms the *outcome* was written down with numbers — a human has to do the read and the count to produce that outcome.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run `run-14-local-llm` reads that report as its entry criterion.
