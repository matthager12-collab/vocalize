# Report: run 14, local language model

Date 2026-09-22. Branch `notes`. Source plan in [project-plan.md](./project-plan.md).

- T-130: done — `llm_manifest.py` pinned from verified download (`0e7ffd5c629ef7719d4cbc04069232580bfa9d9c`), `check_config` over both JSON files, `MIN_RAM_BYTES` (12 GiB), `physical_ram_bytes()` in `local/__init__.py`.
- T-131: done — `llm_worker.py` with `--once`, `--selftest`, token-id prompt building with `split_special_tokens`, think block empty, truncation reply, errors clipped; `selftest_argv` and runtime argv differ in exactly `--offline`.
- T-132: done — `local install --llm [--force]` (RAM gate, download, `check_config` before stamp, selftest), `local uninstall --llm`, LLM block in `local status`, readiness row `llm model`, portal install target `llm`.
- T-133: done — `llm._local` backend with `uv run --offline`; `local` honoured; bare `--cleanup` prefers installed local model; transcript on stdin.
- T-134: done — tests for T-130–T-133 green.
- T-135: done — docs: `docs/dictation.md` local cleanup section, README, CHANGELOG.

Security gate: AST import discipline, `split_special_tokens=True`, no control tokens injected into prompts, `--offline` isolation, offline env vars, config validation before stamp verified in `tests/test_llm_manifest.py`, `tests/test_llm_worker.py`, `tests/test_llm.py`, `tests/test_local_install.py`, `tests/test_readiness.py`.

Deferred: nothing.

validate-exit: PASS
