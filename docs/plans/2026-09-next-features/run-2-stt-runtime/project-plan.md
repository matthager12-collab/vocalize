# Run 2: STT runtime (manifest, installer generalization, worker, uninstall)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 2; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-20 | `vocalize/local/whisper_manifest.py`: base.en, small.en, large-v3-turbo-q5_0 with the sha256s from T-01; `RUNTIME_PACKAGE = "pywhispercpp==1.5.1"`; `MODEL_DIR`; `worker_path()`; `LANGUAGES` allowlist; `is_english_only(model)` | vocalize | T-01 | manifest test: three entries, https URLs pinned to one HF revision, 64-hex sha256, sizes > 0 |
| T-21 | Generalize `vocalize/local/install.py` per design § Installer generalization: `manifest=` on `_model_dir`, `file_is_verified`, `stamp_path`, `write_stamp`, `read_stamp`, `installed`; a `files=` subset for the stamp and `installed()`; `selftest(uv, manifest, ...)` runs `manifest.selftest_argv(model_dir)`; `kokoro_manifest.selftest_argv` reproduces today's argv exactly; Kokoro stamp byte-identical | vocalize | — | existing `test_local_install.py` green unchanged; a stamp written for one whisper model lists only that model; new tests drive the whisper manifest through `opener_for()` with nothing written outside tmp |
| T-22 | `vocalize/local/whisper_worker.py`: `--transcribe`, `--selftest`; `_model_class()` seam; runtime imports inside functions | vocalize | T-20 | AST test: no `pywhispercpp`/`numpy` import at module level; protocol test against a stub model class |
| T-23 | `vocalize local install --stt [--model]`: plan printout, confirmation, download of the selected model only, verify, stamp, `uv run --no-project` selftest with `cwd=tempfile.gettempdir()` (this selftest also pays the one-time ~8 s Metal shader compile so no dictation ever does); `local status` STT block | vocalize | T-20, T-21, T-22 | CLI test with fakes: prints plan, honors decline/`--yes`, skips a verified file, refuses a hash mismatch, idempotent second run |
| T-24 | Move `uv_path()` from `providers/kokoro.py` to `vocalize/local/__init__.py`; re-export; update the Kokoro tests that patch it | vocalize | — | all Kokoro tests green patching the new home |
| T-25 | `vocalize local uninstall --stt` removes the STT model directory and the recorder bundle after a confirmation (or `--yes`); `local status` lists every model file on disk with its size | vocalize | T-23 | test with a populated fake model dir: declined leaves everything, `--yes` removes both, second run says nothing to remove; status output names sizes |

## Role and isolation

- **Role:** Runtime plumbing — mechanical from the Kokoro precedent, except T-21 which edits the shared installer (Sonnet; T-21 reviewed by Opus before commit)
- **Isolation:** shared checkout on `next-features`; `vocalize/local/install.py` is the one file with blast radius (Kokoro must stay byte-identical) — do T-21 first, alone, and commit it before anything else
- **Workload:** 6 source files (2 new: whisper_manifest.py, whisper_worker.py; 4 edited: install.py, cli.py, local/__init__.py, providers/kokoro.py) + 4 test files; install.py generalization weighted ×3 (two manifests must keep passing)

## Entry criteria

- on branch next-features
- suite green at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- manifest pinned (tests)
- worker imports nothing at module level (AST test)
- Kokoro install and provider tests unchanged (regression)
- whisper manifest drives the installer (tests exist)
- uninstall tests green
- local install --stt is a real flag
- local uninstall exists
- kokoro manifest owns its selftest argv
- no runtime dependency leaked into the wheel
- full suite green
- ruff clean
- work committed

## Not machine-checkable (owner present)

- Real install on the reference Mac: `vocalize local install --stt --yes` downloads small.en (488 MB), verifies the hash, pays the Metal warm-up in the selftest; `vocalize local status` shows `STT: ready`. Owner-present; also an entry condition of run 6.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 3 (`run-3-recorder`) reads that report as its entry criterion.
