# Report: run 9, doctor, integrate claude, Setup tab

Date 2026-09-08. Branch `app` (worktree `.claude/worktrees/app`). Full account in [task-report.md](./task-report.md); the review in [../review-0.13.0.md](../review-0.13.0.md) § Run 9 slice.

- T-80: done — `readiness.doctor_rows()` (every provider regardless of chain, the stt and app rows, then `cli path`, `uv`, `swiftc`, `claude`, `shebang`, `hammerspoon`, `services conflict`, `cli start-up`, `app bundle`, `notes folder`) and `vocalize doctor [--json]`, exit 1 on a fail row.
- T-81: done — the Quick Action installer moved into the package as `vocalize/integrate.py` (`hooks/install_quick_action.py` is a thin wrapper) with the four bundles under `vocalize/assets/quick_actions/`; the picker helper moved to `vocalize/speak_options.py` (`hooks/speak_options.py` is a shim); the generic `/speak` skill ships as `vocalize/assets/claude/speak/SKILL.md`; `vocalize integrate claude [--yes]` runs the PATH pre-check, installs the skill to `~/.claude/skills/speak/SKILL.md` (never touching `~/.claude/commands/`), installs the four Quick Actions with the stable `which` paths baked (the `claude` symlink, never its Caskroom target), runs `pbs -update`, and prints the GUI steps: assign only the P and V chords, never D or X, which the app owns. Symlinked destinations are refused.
- T-82: done — the portal's sixth tab, Setup: the nine steps as readiness-driven rows with the existing install routes; `INSTALL_TARGETS` gains `app`, whose worker runs the same `app.py` sequence as the CLI (build, plist, bootout, bootstrap; `tccutil` and the re-grant note only on `rebuilt`); the install progress box now follows whichever panel rendered it.
- T-83: done — tests named above in each file; `test_the_page_behaves_when_driven[setup]` and `[setup_progress]`.
- T-84: done — `docs/installation.md` rewritten around `uv tool install vocalize-cli`, `vocalize app install`, `vocalize integrate claude` and `vocalize doctor`; `docs/app.md` Setup paragraph; README repointed; CHANGELOG.

Security gate: the gate's own rows — `integrate claude --yes` on a scratch HOME lands the skill and exactly four bundles; the baked `claude` path is `/opt/homebrew/bin/claude` with no `Caskroom` string; no inline `<script>`, no external URL in the page. Review (two Opus lenses, three refuters per finding): 17 raw, 7 confirmed (1 medium: the Setup tab showed no install progress and swallowed the re-grant note — fixed; 6 low — all fixed after the review by the release manager: symlinked destinations refused, the skill uses `--overflow` flags instead of an env prefix its allowed-tools could not permit, the templates name `vocalize integrate claude`, verification.md's plist proof globs the moved bundles, the still-checking wording), 10 refuted with reasons. No critical or high finding exists or is open.

Incident: the T-81 writer ran the picker helper for real (`echo "hi there" | python3 vocalize/speak_options.py`) without the `VOCALIZE_BIN` seam; the helper found the owner's installed `vocalize`, spoke the words through Kokoro and overwrote `~/.cache/vocalize/last.wav`. No other state changed. The standing agent rule now names helper scripts explicitly.

Entry note: the suite was red at entry because run 8b's readiness rows saw the app now installed on the owner's Mac; an autouse fixture (`_no_real_app`) hides it, committed before this run's work.

Deferred: nothing. The look-at-it check of the Setup tab's nine steps and the GUI-step wording belongs to run 10.

Suite: 2113 passed, 3 skipped. Ruff clean. Gate 14 of 14.

validate-exit: PASS
