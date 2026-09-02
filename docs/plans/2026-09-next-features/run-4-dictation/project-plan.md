# Run 4: Dictation core, CLI, `[stt]` config, cleanup, Quick Action, STT readiness rows

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 4a; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-40 | `vocalize/dictate.py` state machine: start (claim `~/.cache/vocalize/dictate.session` with `O_CREAT\|O_EXCL`, `stop_playback(remember=True)`, `mkdtemp` 0700, launch recorder via `open -a`, Tink through `audio.play`), stop (recorder alive = `rec.pid` PID exists and its process name is the recorder; touch stop file, wait, silence guard, transcribe, clipboard via `pbcopy` on stdin, Glass, fixed-text notification), cancel (second press within 2 s, or `--cancel`), refuse while transcribing (Pop), dead recorder (no `rec.pid`, dead PID, or another process name) → clear the session, Pop, fixed-text failure notification naming `vocalize listen --check`, exit 1, never a relaunch; max-seconds backstop kill only after the name check, `finally` cleanup incl. the session file, stale `vocalize-dictate-*` sweep (> 24 h) | vocalize | T-22, T-30 | tests with a fake recorder script and fake worker: each transition; transcript never in argv, notification text or a file; tmpdir and session file gone on every exit path including worker crash; a `rec.pid` naming a dead PID or a PID with another process name is never signalled; a recorder that exits 2 before writing `rec.pid` leaves no session and produces the failure notification, and the next press starts again only because the user pressed; two concurrent starts → exactly one recorder |
| T-41 | `vocalize listen` (stdout primitive; Enter/Ctrl-C/max-seconds), `--wav FILE` (trusted input, documented; malformed-WAV negative test), `--toggle`, `vocalize dictate` alias | vocalize | T-40 | CLI tests via `CliRunner` with the fakes |
| T-42 | `[stt]` config: `KNOWN_CONFIG_KEYS`, `_validate_stt_table` (model allowlist, language allowlist, `.en` model + non-`en` language → `ConfigError`, `max_seconds` integer 1–600 else `ConfigError`, `input_device` shape check: ≤ 128 chars, no control characters, no leading `-`, else `ConfigError`), `vocalize settings` lines | vocalize | T-20 | config tests per rule incl. `max_seconds = 0`, `"abc"`, `601` and `input_device = "--foo"`, `"a\nb"` refused; `hooks/speak_options.py` parser test still green |
| T-43 | `--cleanup`: `claude -p` with the fixed prompt from design § Cleanup pass, transcript on stdin, `--disallowedTools '*'`, `timeout=120`, PATH from baked `CLAUDE_BIN`/`CLAUDE_EXTRA_PATH`; timeout, non-zero exit or empty output → raw transcript + "cleanup skipped" notification | vocalize | T-40 | test: argv has the wildcard deny, transcript only on stdin, fallback on non-zero exit, on `TimeoutExpired` and on empty output; an injection-shaped transcript reaches the fake unchanged on stdin and the prompt text contains the data-not-instructions sentence |
| T-44 | `hooks/quick_actions/Dictate with Vocalize.workflow` (copy of Stop Vocalize's no-input shape, id `cards.arda.vocalize.dictate`; script exports `CLAUDE_BIN`/`CLAUDE_EXTRA_PATH` then `exec "$BIN" dictate`); installer `BUNDLE_NAMES` +1 | vocalize | T-41 | bundle tests: no `NSSendTypes`, `serviceProcessesInput = 0`, `inputMethod = 1`, placeholders substituted, `plutil -lint` clean |
| T-45 | Readiness rows for STT: model installed, recorder built, microphone authorization, input device present | vocalize | T-10, T-32 | `vocalize status` shows the four rows with correct states under fakes |

## Role and isolation

- **Role:** Dictation core + CLI — judgement, security-sensitive (Opus); T-44 bundle copy and T-45 rows are mechanical (Sonnet)
- **Isolation:** shared checkout on `next-features`, after runs 1, 2 and 3 (merge run 1's worktree first — both touch `cli.py`)
- **Workload:** dictate.py (new, ~300 lines, state machine with 3 fakes: recorder script, worker, pbcopy — weight ×3), cli.py, config.py, readiness.py, one Quick Action bundle (template copy), 5 test files

## Entry criteria

- on branch next-features
- run 1 merged (readiness module present)
- run 3 validated
- recorder source present
- suite green at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- dictate module exists
- state machine and privacy invariants (resume excluded, run 5)
- [stt] validation
- cleanup pass: deny, stdin, timeout, injection-as-data
- Quick Action bundle shape (pinned to the new artifact after the pre-build audit)
- Quick Action plists lint
- speak_options still parses settings
- STT readiness rows
- dictate command exists
- listen command exists
- listen --check contract
- full suite green
- ruff clean
- work committed

## Not machine-checkable (owner present)

- Hotkey path, cancel/refuse and input device (verification Manual 2, 3, 4b) — owner-present, performed in run 6.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 5 (`run-5-resume`) reads that report as its entry criterion.
