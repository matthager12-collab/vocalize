# Task report: run 10 (release 0.13.0)

## Changelog

- `vocalize/__init__.py` 0.13.0; CHANGELOG; README (`vocalize doctor`, the `[app]` table); docs/roadmap.md; docs/app.md.
- Review fixes: `vocalize/dictate.py` (shape guards), `vocalize/integrate.py` (executable-path refusal, `pbs` timeout), `vocalize/cli.py` (doctor's config-file row; stale status unlinked on rebuild), `vocalize/readiness.py` (services-conflict row removed; caveat wording), `vocalize/app.py` (bootstrap retry), `vocalize/portal.py` (docstring), `vocalize/assets/claude/speak/SKILL.md` (refuse list), and their tests.
- `review-0.13.0.md` § Release review: 0.13.0 (T-91).

## Skipped

The stopwatch latency numbers of manual check 5 (owner's choice) and the log-out-and-in half of check 7 (deferred to the owner's next login). Everything else in scope was done.

## Owner check and check 8

Check 8: after publish, the owner's everyday tool was moved to 0.13.0 with `uv tool install vocalize-cli@latest` while the app was running, the `VocalizeBinary` override that pointed at this worktree was removed, and the app's next press ran the released CLI — recorded in the session's closing summary of 2026-09-08.

## Learnings

- `defaults`, `launchctl` and `tccutil` act on the real user whatever HOME says; redirecting `APP_DIR` alone is not isolation.
- launchd tears a booted-out service down asynchronously; a bootstrap inside that window fails with error 5 and succeeds a second later.
- The Setup tab is built from the readiness rows, not the doctor's; docs must say which.

## Alternatives

- Fixing the speak-selection queueing in the Swift before the freeze — dropped: a medium, a re-grant for every user, the stop chord already covers it; it waits for a Swift patch release.

## Specification (post)

As `project-plan.md`.
