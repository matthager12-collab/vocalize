# Report: run 12, release 0.13.1

Date 2026-09-21. Branch `hold-to-talk` (worktree `.claude/worktrees/hold-to-talk`). Source plan in [project-plan.md](./project-plan.md); adversarial review in [../review-0.13.0.md](../review-0.13.0.md) § 0.13.1.

- T-110: done — review of dictation changes appended to `review-0.13.0.md` § 0.13.1 covering Run 11 (cue trim, hold-to-talk, auto-paste), Spoken Rendering Rules (40 rules, `[speech]` config table), and Run 11b (playback pause & resume with stop-hotkey toggle); no open Critical or High finding.
- T-111: pending publish — manual checks 9–11 and 15–16 verified in runs 11 and 11b; ready for owner squash-merge into `main`, wheel build/publish to PyPI, and digest verification.

Security gate: session nonce matching, paste marker 0600 mode with `O_NOFOLLOW` and `O_NONBLOCK`, monotonic forward session states, and `interrupted.txt` privacy verified (`tests/test_dictate.py`, `tests/test_cli.py`).

Suite: 2,231 passed, 3 skipped. Ruff clean. Pre-publish gate 13 of 14 passed (14th is PyPI publication check).
