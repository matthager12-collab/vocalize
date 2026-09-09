# Task report: run 9 (doctor, integrate claude, Setup tab)

## Changelog

- `vocalize/readiness.py`: `doctor_rows` and the toolchain, conflict, start-up, bundle and notes-folder probes. `vocalize/cli.py`: `doctor`, `integrate claude`.
- `vocalize/integrate.py` (new), `vocalize/speak_options.py` (moved), `vocalize/assets/quick_actions/` (moved), `vocalize/assets/claude/speak/SKILL.md` (new); `hooks/install_quick_action.py` and `hooks/speak_options.py` are wrappers.
- `vocalize/portal.py`: target `app`; `vocalize/assets/portal.html`/`portal.js`: the Setup tab and the shared progress box.
- Tests: test_readiness, test_cli, test_portal, test_portal_assets, portal_page_harness (`setup`, `setup_progress`), test_install_quick_action, test_speak_options, conftest (`_no_real_app`).
- Docs: installation.md (rewritten), app.md, README, CHANGELOG; review-0.13.0.md § Run 9 slice.

## Skipped

Nothing skipped.

## Learnings

- An installed app on the developer's Mac leaks into "these rows exactly" tests unless an autouse fixture hides it, the same way the keychain and the caches are hidden.
- A helper script that falls back to `shutil.which("vocalize")` is a real side effect the moment an agent runs it without the env seam.
- Claude Code's `allowed-tools` prefixes cannot express an env-prefixed command; flags on the CLI are the portable form.
- The page's install progress box must belong to whichever panel rendered last, not to an element id inside one panel.

## Alternatives

- Keeping the Quick Action installer in `hooks/` and importing it from the package — dropped: the wheel must ship the templates and the helper, and `hooks/` is not packaged.
- Skipping the Dictate and Stop Quick Actions when the app is installed — dropped: the bundles are harmless without shortcuts and the GUI steps say which chords to assign.

## Specification (post)

As `project-plan.md`, with the baked paths being the unresolved `which` results and the generic skill using `--overflow` flags.
