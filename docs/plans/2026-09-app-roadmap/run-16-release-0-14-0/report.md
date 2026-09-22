# Report: run 16, release 0.14.0

Date 2026-09-22. Branch `notes`. Source plan in [project-plan.md](./project-plan.md); adversarial review in [../review-0.14.0.md](../review-0.14.0.md).

- T-150: done — CHANGELOG 0.14.0 heading; version bumped to 0.14.0 in `vocalize/__init__.py`; docs cross-check verified for all 0.14.0 commands (`listen`, `dictate`, `resume`, `pause`, `status`, `doctor`, `notes`, `app install`, `app status`, `integrate claude`, `local install`, `auth login`).
- T-151: done — Adversarial review across Phases 13–15b recorded in `docs/plans/2026-09-app-roadmap/review-0.14.0.md`; 0 open Critical or High findings; clean-venv acceptance verified with zero leaked ML runtimes (`pywhispercpp`, `onnxruntime`, `mlx`, `sherpa`, `numpy`, `torch`, `boto3`).
- T-152: deferred for owner — manual checks 12–14 and 17–18; squash-merge `notes` to `main`; publish to PyPI with token (`op run -- uv publish`); verify PyPI digests equal local build sha256.

Security gate: untrusted transcript pipeline sanitized, atomic note writers (0600 mode, O_NOFOLLOW), prompt injection defenses with strict XML boundaries and length caps, custom template path traversal guards (O_NOFOLLOW, 64 KB cap), lossless 0.25s silence segment join, and clean venv verified.

Suite: 2,307 passed, 3 skipped. Ruff clean. Pre-publish checks 14 of 15 passed (check 15 awaits PyPI publish).

validate-exit: PASS
