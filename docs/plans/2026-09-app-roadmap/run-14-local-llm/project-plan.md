# Run 14: The local language model (0.14.0)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 14; contracts in [design.md](../design.md) § Local language-model worker and manifest; proof commands in [verification.md](../verification.md) § Phase 14 exit (local model).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-130 | `llm_manifest.py` pinned from a verified download on the owner's Mac; `check_config` over both JSON files; `MIN_RAM_BYTES`; `RUNTIME_PACKAGE` recorded in the stamp; `physical_ram_bytes()` in `local/__init__.py` | vocalize | T-121 | `tests/test_llm_manifest.py` green: https-only, allowlisted names, `auto_map`/`model_file`/`custom_pipelines`/foreign `tokenizer_class`/wrong `model_type` refused |
| T-131 | `llm_worker.py`: `--once`, `--selftest`, token-id prompt building with `split_special_tokens`, think block empty, truncation reply, errors clipped; `selftest_argv` (online) and the runtime argv (`--offline`) differ in exactly that flag | vocalize | T-130 | AST import-discipline test; fake-tokenizer test asserts `split_special_tokens=True` and no control-token id from user text; selftest fixed-cleanup assertion; a test asserts the two argvs differ only by `--offline` |
| T-132 | `local install --llm [--force]` (RAM gate, download, `check_config` before the stamp, selftest), `local uninstall --llm`, LLM block in `local status`, readiness row `llm model`, portal install target `llm` | vocalize | T-130 | CliRunner tests; the readiness reason names measured and required RAM and the two cloud alternatives when gated |
| T-133 | `llm._local` backend with `uv run --offline`; `local` honoured; bare `--cleanup` prefers an installed local model; resident session only if T-121 demanded it | vocalize | T-131, T-132 | fake-uv test: `--offline` in argv, request on stdin, offline env vars, transcript never in argv |
| T-134 | Tests for T-130–T-133 | vocalize | T-133 | green |
| T-135 | Docs: `docs/dictation.md` local cleanup section, README, CHANGELOG | vocalize | T-133 | present |

## Role and isolation

- **Role:** Opus — token-id prompt building (`llm_worker.py`, DEC-027's constraint that no downloaded chat template is ever evaluated) and manifest hardening (`llm_manifest.py`'s refusal surface for `auto_map`/`model_file`/`custom_pipelines`/foreign `tokenizer_class`) both carry security judgement that a mechanical pass would miss.
- **Branch:** `notes` (off `main`, shared with Phases 13 and 15).
- **Isolation:** `vocalize/local/` and `vocalize/llm.py` — the same files Phase 13's runtime-plumbing role touches; do this run after Phase 13 is merged on `notes`, not beside it.
- **Workload:** 7 source files (2 new: `llm_manifest.py`, `llm_worker.py`; 5 edited: `local/__init__.py`, `cli.py`, `readiness.py`, `portal.py`, `llm.py`) + 6 test files (2 new: `test_llm_manifest.py`, `test_llm_worker.py`; 4 edited: `test_cli.py`, `test_local_install.py`, `test_readiness.py`, `test_llm.py`).

## Entry criteria

- T-121 done (the mlx-lm spike, § LLM in `spike-notes.md`, fully answered — the manifest cannot be pinned before the spike confirms `load()`/`generate()` signatures, offline env vars, and the think-off ChatML string).
- On its own branch, not `main`.
- Run 13 (`run-13-spikes`) reports `validate-exit: PASS`.
- Suite green at entry; ruff clean.

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- manifest hardened (`tests/test_llm_manifest.py`)
- worker discipline (`tests/test_llm_worker.py`: AST import-discipline, `split_special_tokens`, truncation reply, selftest and runtime argvs differ only by `--offline`)
- install lifecycle (`tests/test_cli.py`, `tests/test_local_install.py`, `tests/test_readiness.py` filtered to `llm`)
- local backend (`tests/test_llm.py` filtered to `local`: `--offline` in argv, request on stdin, offline env vars)
- no runtime leaked into the wheel (clean-venv acceptance)
- full suite green
- ruff clean
- work committed

## Not machine-checkable

- **Real install on this Mac** (verification.md's sixth Phase 14 row): `vocalize local install --llm --yes` against the real, pinned manifest, followed by `vocalize local status` reading `LLM: ready`. This needs the actual ~3 GB Qwen3.5-4B download and enough free RAM (`MIN_RAM_BYTES`, 12 GiB) on the reference Mac — owner-present, per T-130.
- **T-130's hash pinning itself.** `tests/test_llm_manifest.py` proves the manifest's *shape* (https-only, allowlisted names, the refusal cases) but not that its sha256s match a real file — that requires the owner to run a genuine ~3 GB download on their own Mac and paste the verified hashes into `llm_manifest.py` before this run's real-install check can mean anything. Until that download happens, the manifest is structurally correct but unverified.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 15 (`run-15-notes`) reads that report as its entry criterion.
