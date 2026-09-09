# Decisions: local-first defaults, the menu-bar app, and recorded notes

Continues the sequence from [../2026-09-next-features/decisions.md](../2026-09-next-features/decisions.md) (DEC-001 to DEC-019). Owner decisions were taken on 2026-09-04 against [the analysis](../../research/2026-09-04-app-roadmap-analysis.md); the design picks on 2026-09-06 after an authored-and-critiqued alternatives round.

| # | Question | Status | Decision | Round |
|---|---|---|---|---|
| DEC-020 | How is the app shipped? | Decided | B — compiled on the user's Mac by `vocalize app install`; no Developer ID, notarization or prebuilt binary | R1 |
| DEC-021 | Does the app take an Accessibility grant? | Decided | B — one grant, for speak-the-selection and paste; dictation hotkeys need none | R1 |
| DEC-022 | What is the default chain? | Decided | B — `("kokoro", "say")`, with a note when `say` speaks because Kokoro is missing | R1 |
| DEC-023 | Do notes store the transcript? | Decided | A — yes, in the note, 0600 | R1 |
| DEC-024 | Which cloud path for cleanup and notes? | Decided | C — both; `claude -p` first, the Anthropic API second | R1 |
| DEC-025 | Which keychain backend on macOS? | Decided | B — `/usr/bin/security` behind `_backend()`, gated on the check (DEC-035) | R1 |
| DEC-026 | Which speech-to-text engine for 0.14? | Decided | C — whisper through 0.13; a 2-hour Parakeet spike gates 0.14 (DEC-034) | R1 |
| DEC-027 | Is the downloaded chat template evaluated? | Decided | B — never; the worker builds token ids from its own constant | R1 |
| DEC-028 | How does the app talk to vocalize? | Decided | A — stateless shell-out, one process per hotkey, no baked binary | R2 |
| DEC-029 | How is `vocalize notes` composed? | Decided | A — one command, one module; the notes folder is the ledger | R2 |
| DEC-030 | How do cleanup and summaries reach three backends? | Decided | A — one `llm.py` with `_complete`; enum `off\|local\|claude-cli\|anthropic` | R2 |
| DEC-031 | How is the cue kept out of the recording? | Decided | B — trim the take in dictate.py after the first growth of `take.wav`; the recorder stays frozen | R2 |
| DEC-032 | How many releases, and where does the Swift source freeze? | Decided | B — four releases; the Swift source ships complete in 0.13.0 so 0.13.1 is Python only | R2 |
| DEC-033 | Which hotkey backend? | Decided | A — Carbon; the T-60 spike passed with Claude desktop, Ghostty and full-screen Claude in front | R2, decided run 7 |
| DEC-034 | Parakeet or whisper for 0.14? | Deferred | to the spike T-120; both branches planned | R2 |
| DEC-035 | Does the `security` backend hold across rebuilt callers? | Decided | A — it holds; the backend is built (run 4, 2026-09-07) | R2 |
| DEC-036 | How does a read pause, and how is it reached? | Decided | C — `vocalize pause` on the remembered stop, plus opt-in `[app] stop_hotkey = "pause"`; `stop` and the one-hour life unchanged | R3 |
| DEC-037 | How does a recording pause without touching the frozen recorder? | Decided | C — stop, segment, relaunch, join in Python; `--max` per segment under a new `[stt] max_take_seconds` | R3 |

---

## Round 1 (owner decisions, 2026-09-04)

### DEC-020: How is the app shipped?

**Date**: 2026-09-04
**Decided by**: Mat
**Status**: Decided

**Context**: A downloaded app is quarantined and needs a Developer ID certificate plus notarization to open without the "Open Anyway" flow. The recorder already proves the other route: a Swift source compiled and ad-hoc signed on the user's own Mac carries no quarantine and launches without a dialog.

| Option | Description | Trade-offs |
|---|---|---|
| A | Homebrew cask plus a formula, or a signed `.pkg` | A second install channel, a certificate at $99 a year, notarization on every release; baked paths |
| B | `vocalize app install` compiles the app locally with `swiftc`, exactly like the recorder | Command Line Tools required; about a minute to compile; no certificate, no notarization, no download; a same-time install from PyPI is impossible, so it is two commands |
| C | Stay CLI plus Hammerspoon | Two apps and an Accessibility grant to ask of anyone else; the Services shortcut stays a GUI-only step |

**Recommendation**: B.

**Decision**: B.

**Consequences**: Every change to the app's Swift source is a new ad-hoc identity and a new Accessibility grant, so the source must ship complete and change rarely (DEC-032). Users without Command Line Tools are out of scope. Two commands in the README, and the first run offers to build the app.

**Applied to**:
- [design.md](./design.md) § Approach, § LaunchAgent and bundle identity
- [plan.md](./plan.md) § Phase 7, § Phase 8

---

### DEC-021: Does the app take an Accessibility grant?

**Date**: 2026-09-04
**Decided by**: Mat
**Status**: Decided

**Context**: Carbon hotkeys need no permission, but speak-the-selection must copy the selection first, and a synthetic Command-C needs Accessibility. Auto-paste needs the same grant.

| Option | Description | Trade-offs |
|---|---|---|
| A | No grant; keep the speak-selection hotkey in Hammerspoon | Hammerspoon stays a dependency for one hotkey |
| B | One Accessibility grant to the app | One GUI-only click; a rebuild resets it; dictation still needs none |

**Recommendation**: B.

**Decision**: B.

**Consequences**: `app install` runs `tccutil reset Accessibility` on a rebuild and says so. Auto-paste rides the same grant (plan.md two-way door). If the hotkey spike fails (DEC-033), the event-monitor fallback also runs under this grant.

**Applied to**:
- [design.md](./design.md) § Hotkey to vocalize, § Auto-paste
- [plan.md](./plan.md) T-70, T-72

---

### DEC-022: What is the default chain?

**Date**: 2026-09-04
**Decided by**: Mat
**Status**: Decided

**Context**: `config.DEFAULT_CHAIN` is still `("elevenlabs", "say")` and the docs lead with an API key. Local first is the stated default. Kokoro needs an opt-in download, and the owner dislikes the `say` voices.

| Option | Description | Trade-offs |
|---|---|---|
| A | `("kokoro",)` alone; fail loud with the install command | A fresh install with no model is silent until the user runs the install |
| B | `("kokoro", "say")` with one line naming the missing install when `say` speaks | A keyless fresh Mac still speaks; the note makes the fallback visible |

**Recommendation**: B.

**Decision**: B, with the note.

**Consequences**: Users who never set a chain hear `say` after the upgrade until they install Kokoro; the note tells them how. Four tests pin the old literal and change with it. The flat ElevenLabs key scheme and the cache-key format are untouched.

**Applied to**:
- [design.md](./design.md) § Fallback note
- [plan.md](./plan.md) T-01 to T-04

---

### DEC-023: Do notes store the transcript?

**Date**: 2026-09-04
**Decided by**: Mat
**Status**: Decided

**Context**: DEC-007 made dictation never store a transcript. A note is meant to be kept, and a summary that cannot be checked against its transcript is not a note.

| Option | Description | Trade-offs |
|---|---|---|
| A | The note carries the full timed transcript, 0600, in the user's notes folder | Plaintext on disk by design; iCloud sync of `~/Documents` moves it off the machine regardless of the egress line |
| B | Summary only; the transcript behind a flag | Cheaper on disk; the summary is unverifiable |

**Recommendation**: A.

**Decision**: A.

**Consequences**: DEC-007 is unchanged for dictation and does not apply to notes. `docs/notes.md` § Privacy states the file mode, the `claude-cli` history stub, the egress line and the iCloud caveat. `vocalize doctor` warns when the folder resolves under `~/Library/Mobile Documents`.

**Applied to**:
- [design.md](./design.md) § Notes, § Note file
- [plan.md](./plan.md) T-141, T-145

---

### DEC-024: Which cloud path for cleanup and notes?

**Date**: 2026-09-04
**Decided by**: Mat
**Status**: Decided

**Context**: Two ways to reach a frontier model: `claude -p` on the owner's subscription (no key; writes a title stub into Claude Code history even with session persistence off) and the Anthropic Messages API with a stored key (no history; needs the key slot; budgets in the ledger).

| Option | Description | Trade-offs |
|---|---|---|
| A | API only | Needs a key on day one |
| B | `claude -p` only | No budget, history stub |
| C | Both, as enum values; `claude -p` built first | One more backend to test; the user picks per feature |

**Recommendation**: C.

**Decision**: C, `claude-cli` first.

**Consequences**: The enum values `claude-cli` and `anthropic` become config contract. The `anthropic` key slot, budget and ledger row ship in 0.12.0 with the backend; an unset budget defaults to 2,000,000 characters a month and the ledger is per Mac. `_claude_env` must strip `ANTHROPIC_*` so the subscription path never bills the stored key. `claude-cli` draws on the same Claude Code usage pool as coding sessions, and its session must exclude user-scope settings (hooks, skills, `CLAUDE.md`) as well as project scope; the docs say both.

**Applied to**:
- [design.md](./design.md) § Cleanup and summaries through llm.py, § Key slots and the Keys tab
- [plan.md](./plan.md) T-20, T-27, T-40 to T-43

---

### DEC-025: Which keychain backend on macOS?

**Date**: 2026-09-04
**Decided by**: Mat
**Status**: Decided

**Context**: keyring adds items with no explicit access list, so macOS pins each item to the creating binary's code hash; a rebuilt or different Python cannot read it without a prompt (the "Electron gotcha"). `/usr/bin/security` is Apple-signed and stable, and existing `gh` items on this keychain carry that kind of entry. Apple's own engineers call the access-list mechanics "a dark art", so the claim needs a check.

| Option | Description | Trade-offs |
|---|---|---|
| A | Keep keyring; document the Always Allow click | Zero code; the prompt returns on every Python rebuild |
| B | `security`-backed object behind `_backend()` on Darwin, after a 30-minute check | Secret on stdin, never argv; a migration on login; keyring stays elsewhere |

**Recommendation**: B, gated on the check.

**Decision**: B, gated on DEC-035.

**Consequences**: Tests drive a fake `security` script. If the check fails, A ships with the doc section and DEC-035 records why.

**Applied to**:
- [design.md](./design.md) § Keychain through security, § Keychain via `security`
- [plan.md](./plan.md) T-30 to T-33

---

### DEC-026: Which speech-to-text engine for 0.14?

**Date**: 2026-09-04
**Decided by**: Mat
**Status**: Decided

**Context**: Parakeet TDT 0.6B via sherpa-onnx reports about 20 % fewer errors than whisper turbo on the public leaderboard, native punctuation, and long-audio strength, but its speed and memory on an M4 are unmeasured, it runs on CPU where whisper uses Metal, and it is a second engine with a second manifest and a tar extraction. Beam search fixes the known "toget" merge on whisper.

| Option | Description | Trade-offs |
|---|---|---|
| A | Adopt Parakeet for 16 GB now | Accuracy play on unmeasured numbers; 10–16 h |
| B | Whisper only, turbo q8_0 plus beam search | One engine; the accuracy gap stays |
| C | Whisper through 0.13; a 2-hour spike on the owner's own clips gates 0.14, with both outcomes planned | Two hours before the decision; the plan carries a conditional phase |

**Recommendation**: C.

**Decision**: C. Go for Parakeet only if it has fewer jargon errors, stop-to-clipboard no slower than `small.en`, and peak RSS under 1.5 GB.

**Consequences**: `[stt] engine` and `dictate.worker_argv` dispatch exist only if the spike passes; either way the `--segments` worker contract is engine-neutral and `[notes] model` lets the 8 GB tier use turbo for notes.

**Applied to**:
- [design.md](./design.md) § Whisper worker `--segments`
- [plan.md](./plan.md) T-120, T-122

---

### DEC-027: Is the downloaded chat template evaluated?

**Date**: 2026-09-04
**Decided by**: Mat
**Status**: Decided

**Context**: The Qwen tokenizer evaluates a Jinja `chat_template` from the model download. The standing rule is that nothing downloaded is executed. The critique of the chosen design also found that tokenizing a prompt string lets a spoken control-token literal become a real control token.

| Option | Description | Trade-offs |
|---|---|---|
| A | Pin the downloaded template's sha256 and let the tokenizer evaluate it | Still evaluates downloaded content |
| B | Ship the template inside the wheel and ignore the download | A shipped Jinja is still evaluated |
| C | The worker builds the prompt as token ids from its own string constant; `apply_chat_template` is never called; `system` and `text` are encoded with `split_special_tokens=True` | Strictest reading of the rule; the constant must match the model card, checked by the install selftest |

**Recommendation**: C (the owner chose B; the design round tightened it to C, which contains B).

**Decision**: C.

**Consequences**: A wrong template fails at install, not at first dictation. The manifest downloads no `.jinja` and no `.py`, and `check_config` rejects `auto_map`, `model_file`, `custom_pipelines` and foreign tokenizer classes in both JSON files.

**Applied to**:
- [design.md](./design.md) § Local language-model worker and manifest
- [plan.md](./plan.md) T-130, T-131

---

## Round 2 (design picks, 2026-09-06)

### DEC-028: How does the app talk to vocalize?

**Date**: 2026-09-06
**Decided by**: Mat (recommendation accepted after an authored-and-critiqued round)
**Status**: Decided

**Context**: The menu-bar app must dispatch hotkeys, show a recording indicator, find the CLI after upgrades, and survive crashes. Two candidates were authored from opposite stances and each critiqued by an independent reviewer.

| Option | Description | Trade-offs |
|---|---|---|
| A | Stateless shell-out: one `vocalize …` process per hotkey; indicator from a state word in `dictate.session`; one LaunchAgent with KeepAlive; binary found at spawn time from three paths, never baked | No protocol, no resident Python; an upgrade lands on the next press; stop never waits behind a transcription; every Swift edit is a re-grant; Python start-up per press; 38–50 h after the critique |
| B | Resident helper: the app keeps `vocalize app serve` alive and exchanges JSON lines | Behaviour changes without a rebuild; but the protocol is baked into the signed app, spawned CLIs share the helper's stdout (the portal's one-time code would land there), a serial loop can block stop for minutes, and `SMAppService` from `~/.cache` is unverified; 44–58 h |

**Recommendation**: A, with its critique's fixes and three grafts from B (`vocalize doctor` and `readiness.doctor_rows()`, the frontmost-app check before paste, fixed notices for a failed `clip`).

**Decision**: A.

**Consequences**: The verbs `dictate [--start|--stop]`, `clip`, `stop`, `listen --cancel`, `settings`, `portal` and the `key=value` lines of `settings` are a compatibility contract for one minor release past any change. The files `dictate.session` (state, nonce), `app.status`, `app.log`, `dictate.copied` and `<workdir>/cue` are contracts third parties will read; the app treats every one of them as untrusted (the binary override is stat-checked, the paste marker must carry the session's nonce). Bundle id `cards.arda.vocalize.app`, LaunchAgent label, executable name and location (`~/Library/Application Support/vocalize/Vocalize.app`, outside the cache folder the README calls safe to delete) are keyed by the Accessibility entry and the plist; renaming or moving orphans both.

**Applied to**:
- [design.md](./design.md) § Hotkey to vocalize, § App spawn contract, § Files under `~/.cache/vocalize`, § LaunchAgent and bundle identity
- [plan.md](./plan.md) § Phase 8, § Phase 9

---

### DEC-029: How is `vocalize notes` composed?

**Date**: 2026-09-06
**Decided by**: Mat (recommendation accepted)
**Status**: Decided

**Context**: Recorded-note processing needs convert, transcribe, summarize and write per file, idempotence over a folder, templates, and a text-input path for Plaud transcripts.

| Option | Description | Trade-offs |
|---|---|---|
| A | One command, one module `notes.py`; text input is a `.txt` file; the notes folder's frontmatter is the ledger | Smallest surface; every seam exists; the wizard must render `[notes]` explicitly; 33–36 h after the critique |
| B | Three commands (`transcribe`, `summarize`, `notes`) and a resident model session | Pipes with the Plaud CLI; two commands nobody asked for; a resident Qwen competes with dictation for memory on 16 GB; 36–46 h |

**Recommendation**: A, with the critique's fixes (the wizard render in 0.12.0, the inactivity timeout, per-destination caps, the self-ingestion guard, the run lock, the stale sweep, `--summarizer` replacing `--cloud`) and five grafts from B (the inactivity timeout, the Anthropic request details, the enum on the CLI, the knowledge-base frontmatter keys, utf-8 decoding).

**Decision**: A.

**Consequences**: The note filename scheme and frontmatter keys become contract, including `trust: "untrusted-transcript"`: a note is third-party text and is never fed to a model as instructions, and the knowledge-base handoff keeps that key. No ledger file; the done check matches hash and source path and prints every skip; deleting a note re-processes its source; every write is atomic. The `--segments` worker contract is shared with any later engine.

**Applied to**:
- [design.md](./design.md) § Notes, § Note file, § Whisper worker `--segments`
- [plan.md](./plan.md) T-23, § Phase 15

---

### DEC-030: How do cleanup and summaries reach three backends?

**Date**: 2026-09-06
**Decided by**: Mat (recommendation accepted)
**Status**: Decided

**Context**: Dictation cleanup (issue #3) and note summaries must reach a local mlx-lm worker, `claude -p`, and the Anthropic API with one data-boundary prompt, timeouts, the egress line, budgets, and no transcript in argv.

| Option | Description | Trade-offs |
|---|---|---|
| A | One module `llm.py`: `cleanup_transcript`, `summarize`, a private `_complete` with an if/elif over three private backends | Smallest; the spoken `verbatim` keyword handled in Python so it works from the hotkey; missed plumbing (the slot must reach the CLI and portal) and two holes (the API key leaking into the `claude -p` environment; special tokens from transcript text) fixed as requirements; 27–38 h |
| B | A `vocalize/llm/` package with a registry and one module per backend | Cleaner egress strings; more shape for three small functions; no spoken keyword; still evaluates a template; 24–34 h |

**Recommendation**: A, with the critique's fixes and four grafts from B (the full enum accepted from 0.12.0 with `local` refused at run time, the backend check before the egress line, `uv run --offline`, bare `--cleanup` preferring an installed local model).

**Decision**: A.

**Consequences**: Enum values, the `anthropic` ledger name and `[providers.anthropic] monthly_chars`, the keychain slot `anthropic-api-key` and env var `ANTHROPIC_API_KEY`, the worker JSON-line protocol and `~/.cache/vocalize/models/qwen`, the spoken first-word rule, and bool coercion all become contract. `hooks/speak_options.py` keeps its own `claude -p` copy.

**Applied to**:
- [design.md](./design.md) § Cleanup and summaries through llm.py, § llm.py API
- [plan.md](./plan.md) § Phase 3, § Phase 14

---

### DEC-031: How is the cue kept out of the recording?

**Date**: 2026-09-06
**Decided by**: Mat (recommendation accepted with the app pick)
**Status**: Decided

**Context**: Issue #2: the "talk now" cue must follow the open microphone and never be recorded. The recorder's Swift source is frozen (a change is a microphone re-grant for every user), so a `--go` flag in the recorder is off the table.

| Option | Description | Trade-offs |
|---|---|---|
| A | Recorder `--go` flag: warm the input, wait for a marker, then capture | Clean; costs every user a microphone re-grant |
| B | dictate.py waits for the first growth of `take.wav` (the real open-microphone signal), plays the cue, records the elapsed seconds, and trims that many frames from the head of the take | Recorder untouched; depends on incremental flushing (1-hour spike); under-trims by at most one flush, never over-trims; a fallback keeps today's order with no trim |
| C | Head-trim from `rec.pid` plus the play duration and a tail knob | Simpler anchor; can clip a first phoneme on Bluetooth |

**Recommendation**: B.

**Decision**: B, gated on the cue spike (T-100) with the fallback branch planned.

**Consequences**: Reopening this later for a `--go` flag costs a recorder rebuild and a re-grant for everyone. Hold mode plays no cue and writes no trim. In no cue mode and neither branch does a cue word reach the worker (tested by frame count).

**Applied to**:
- [design.md](./design.md) § Dictation with the cue trim
- [plan.md](./plan.md) T-100, T-101

---

### DEC-032: How many releases, and where does the Swift source freeze?

**Date**: 2026-09-06
**Decided by**: Mat ("as small chunks as possible")
**Status**: Decided

**Context**: The app slice grew to 38–50 h in the critique. Every edit to the app's Swift source after the first grant costs the user an Accessibility re-grant.

| Option | Description | Trade-offs |
|---|---|---|
| A | One 0.13.0 at 55–70 h | One long run |
| B | 0.13.0 (app, doctor, integrate, setup) then 0.13.1 (cue trim, hold-to-talk, paste), with the Swift dispatch for hold and the paste watcher already compiled into 0.13.0 | 0.13.1 is Python only, no re-grant; `dictate_mode = "hold"` is parsed by the 0.13.0 validator and resolved to toggle with one warning until 0.13.1 (run 7 chose accept-and-warn over refuse, so a rollback from 0.13.1 never bricks the CLI) |

**Recommendation**: B.

**Decision**: B, and one run per phase throughout (twenty runs, after the two pause runs were inserted on 2026-09-07).

**Consequences**: The 0.13.0 Swift source must be complete at ship time (both hotkey backends selected at build time per DEC-033, hold dispatch, the paste watcher); the 0.13.1 exit gate refuses any diff under `vocalize/menubar/`. A Swift defect found after 0.13.0 ships as an out-of-band patch release that costs one Accessibility re-grant; that is the accepted price of a locally compiled app, not a process failure. Phase 8 runs as two chunks (Swift, then Python) so no run exceeds about 20 hours.

**Applied to**:
- [design.md](./design.md) § Approach, § LaunchAgent and bundle identity
- [plan.md](./plan.md) § Phases 7–12, § Suggested run boundaries

---

### DEC-033: Which hotkey backend?

**Date**: 2026-09-06; decided 2026-09-07
**Decided by**: Mat, on the T-60 spike's three results
**Status**: Decided

**Context**: Carbon `RegisterEventHotKey` needs no permission and delivers key-up, but a report says self-drawn apps (Claude Code desktop is Electron) can swallow Carbon hotkeys. The alternative is an `NSEvent` global monitor, which needs the Accessibility grant the app already takes (DEC-021).

| Option | Description | Trade-offs |
|---|---|---|
| A | Carbon | No permission for dictation; unverified with Electron frontmost |
| B | Global event monitor under the existing grant | Dictation then depends on Accessibility too; `app.status` reports `hotkey_backend: monitor` |

**Recommendation**: A if the spike passes in all three frontmost cases; otherwise B, and this entry gains a sentence amending DEC-021's "dictation needs none".

**Decision**: A, Carbon. The T-60 spike (2026-09-07, a throwaway Swift probe registering control-option-command-D through `RegisterEventHotKey`, run with `NSApplication` in accessory mode) delivered one key-down and one key-up in all three frontmost cases: Claude desktop in a normal window (held 2 s), Ghostty, a self-drawn terminal (held 3 s), and Claude desktop full-screen (held 3 s). Register status 0 with Hammerspoon running on S and X. Both backends are still compiled into the app; the build-time constant selects Carbon. Results in [spike-notes.md](./spike-notes.md) § Hotkeys.

**Consequences**: Either way the Swift source is complete in 0.13.0. The backend is a build-time constant with no runtime fallback: a later macOS regression of the chosen backend shows as `hotkeys: failed` in `app.status` and a doctor row, and switching costs a rebuild and a re-grant. Accepted as a one-way door; a self-firing hotkey test at launch was considered and rejected as more code than the failure warrants.

**Applied to**:
- [design.md](./design.md) § LaunchAgent and bundle identity
- [plan.md](./plan.md) T-60, T-70

---

### DEC-034: Parakeet or whisper for 0.14?

**Date**: 2026-09-06
**Decided by**: deferred to the owner after T-120
**Status**: Deferred

**Context**: DEC-026 set the gate. This entry records the outcome.

| Option | Description | Trade-offs |
|---|---|---|
| A | Go: Parakeet via sherpa-onnx becomes the 16 GB engine; T-122 ships | A second manifest and worker; tar extraction with an allowlist; CC-BY attribution |
| B | No-go: whisper stays the only engine | Nothing to build |

**Recommendation**: Follow the numbers: fewer jargon misses, no slower than `small.en`, RSS under 1.5 GB.

**Decision**: Pending the spike.

**Consequences**: Recorded with the measured numbers when decided.

**Applied to**:
- [plan.md](./plan.md) T-120, T-122
- [verification.md](./verification.md) § Phase 13 exit

---

### DEC-035: Does the `security` backend hold across rebuilt callers?

**Date**: 2026-09-07
**Decided by**: Mat (the check's result accepted)
**Status**: Decided

**Context**: DEC-025 set the direction. The claim that an item written through `/usr/bin/security` is readable from any later Python without a prompt is plausible and unverified.

| Option | Description | Trade-offs |
|---|---|---|
| A | Check passes: build the backend (T-31 branch A) | 4–6 h |
| B | Check fails: keep keyring, document the Always Allow click (T-31 branch B) | 0.5 h |

**Recommendation**: Run the check first; do not build on the claim.

**Decision**: A. The check ran on 2026-09-07 on the reference Mac (macOS 26.5.1) against throwaway items, every command under a 25 s timeout so a dialog would have shown as exit 124:

```
printf 'add-generic-password -a probe -s vocalize-check -j "validated …" -w … -U\n' | security -i     # rc 0
security find-generic-password -s vocalize-check -a probe -w   spawned from:
    the worktree Python (uv CPython 3.12)                       # rc 0, value correct
    the uv-tool vocalize's Python (~/.local/share/uv/tools)     # rc 0, value correct
    /usr/bin/python3 (Apple)                                    # rc 0, value correct
    a freshly compiled, ad-hoc signed Swift binary              # rc 0, value correct
security find-generic-password -s vocalize-check -a probe      # "icmt"<blob>="validated …", no -w needed
python -c 'keyring.set_password("vocalize-check2", …)'          # an item keyring wrote
security delete-generic-password -s vocalize-check2 -a probe2  # rc 0, no dialog
printf 'add-generic-password … -U' | security -i               # rc 0, no dialog; read-back is the new value
security delete-generic-password … (both items)                # rc 0
```

No dialog appeared at any step.

**Consequences**: `auth._backend()` returns a `_SecurityKeychain` on macOS (keyring elsewhere); the secret travels on stdin; the comment attribute carries the validation date; `login` replaces an older keyring-written item in one `-U` write. Keys containing a double quote or a backslash are refused before they reach the tool. The `security` tool's own exit codes (44 = not found) are the contract.

**Applied to**:
- [plan.md](./plan.md) T-30, T-31
- [verification.md](./verification.md) § Phase 4 exit
- [design.md](./design.md) § Keychain through security
- `vocalize/auth.py` (`_SecurityKeychain`, `_default_backend`, `store_key`, `validated_on`), run 4 commit on `local-first`

---

### DEC-036: How does a read pause, and how is it reached?

**Date**: 2026-09-07
**Decided by**: Mat (all five owner questions answered "as recommended", 2026-09-07)
**Status**: Decided

**Context**: "The ability to pause and resume" was missed in every requirement. Playback already stops and remembers: a dictation calls `audio.stop_playback(remember=True)`, the playing process saves an interrupt record, and `vocalize resume` continues it (DEC-003, DEC-012, DEC-014). What is missing is a verb, a way to reach it from the keyboard, and an answer on how long a deliberate pause may sit. `VocalizeApp.swift` ships complete in 0.13.0 and every later edit is an Accessibility re-grant (DEC-032), so a new chord is off the table.

| Option | Description | Trade-offs |
|---|---|---|
| A | `SIGSTOP` the live player, `SIGCONT` on resume | True pause in place, no offset maths — but `play()` holds the machine-wide `play.lock` until the player exits, so a suspended player blocks every later read on the Mac, the `/speak` hook included; and the PID identity re-check has to survive an open-ended suspension |
| B | `vocalize pause` = the remembered stop that already ships, continued by `vocalize resume`; terminal only | No new file, format or lifetime; every path already tested. But a pause you have to type is not a pause a listener takes mid-sentence |
| C | B, plus `[app] stop_hotkey = "stop"\|"pause"` (default `stop`) so the existing stop chord becomes play/pause | One config key and one branch, Python only, no re-grant; opt-in, so scripts and the hook keep today's meaning. Cost: with it on, every stop press writes `interrupted.txt` |
| D | Make `vocalize stop` a play/pause toggle by default, with `stop --forget` as the hard stop | Best reach, no config key — but it rewrites shipped `stop` tests and turns the one "make it stop" command into one that can start speaking |

**Recommendation**: C, default `stop`. Keep `MAX_AGE` at one hour, and leave `vocalize stop` alone.

**Decision**: C. `vocalize pause` is the verb; `interrupted.wait_for_record(since)` (moved out of `dictate.py`, one mechanism for both callers) decides what the message says; `[app] stop_hotkey = "pause"` gives the owner a chord that pauses and resumes without a rebuild. `vocalize stop` keeps today's meaning exactly — it silences, records nothing, and leaves a saved record alone — so no shipped test is rewritten and `resume --forget` stays the explicit discard. The paused read lives one hour, the same as an interrupt: that hour is the privacy budget `interrupted.txt` depends on (DEC-012e), and a deliberate pause makes that file more frequent, not less sensitive. `_RESUME_REWIND = 1.0` inside `slice_from` so a continuation overlaps the last word.

**Consequences**: `vocalize pause` writes a read's words in plaintext at `~/.cache/vocalize/interrupted.txt`, 0600, for up to an hour; the docs must say so. The hour is a real cliff — pause, go to lunch, and the read is gone with no warning beyond the message printed at pause time. Two resumes race harmlessly but audibly (the read plays twice, back to back on the lock); documented, not fixed. The `/speak` hook's timeout `killpg` never goes through `stop_playback`, so a read killed that way still leaves no record and cannot be resumed; that is a docs line, not a fix. Saying no to the hotkey key drops run 11b to about 3.5 hours.

**Applied to**:
- [design.md](./design.md) § Pause and resume
- [plan.md](./plan.md) § Phase 11b, T-106–T-109
- [verification.md](./verification.md) § Phase 11b exit, manual checks 15 and 16
- [run-11b-playback-pause/project-plan.md](./run-11b-playback-pause/project-plan.md)

---

### DEC-037: How does a recording pause without touching the frozen recorder?

**Date**: 2026-09-07
**Decided by**: Mat (all five owner questions answered "as recommended", 2026-09-07)
**Status**: Decided

**Context**: Recording has no pause at all. `dictate.py` runs one take through the Swift recorder and then the whisper worker. The recorder's source is frozen: every rebuild costs each user a new microphone grant (DEC-010), which is exactly why DEC-031 solved the cue trim in `dictate.py` rather than with a recorder flag. The recorder also bounds `--max` to 1..600 seconds in its own parser and again in `config.py`, so how the budget is spent decides whether a paused memo can run past ten minutes. Separately, `vocalize notes` as run 15 scopes it never opens a microphone.

| Option | Description | Trade-offs |
|---|---|---|
| A | Add a pause verb to `VocalizeRecorder.swift` | Instant, no seam — and a new microphone grant for every user, plus a second frozen source unfrozen |
| B | `SIGSTOP` the recorder process | Untested against `AVAudioEngine` mid-I/O; the recorder's own handler treats every trapped signal as finalise-and-exit, and `SIGSTOP` is not trapped at all. Needs its own spike |
| C | Stop the recorder cleanly, keep the finished WAV as a segment, relaunch on resume, join the segments with stdlib `wave` before transcription | Reuses only proven paths (`_stop_file`, `_wait_for_exit`, `_launch_recorder`) and the recorder's fixed 16 kHz mono 16-bit output makes the join lossless. Cost: a real gap in captured audio at each seam |
| D | C, but with `max_seconds` as one budget for the whole take | No new config key — and the take is still capped at the recorder's frozen 600 s, so pause buys nothing for long memos |

**Recommendation**: C, with `--max` per segment and a new `[stt] max_take_seconds` bounding the whole take.

**Decision**: C. Pause stops the recorder through its own stop file, trims the cue from that segment, files it as `take.NNN.wav` and writes a `paused` marker; resume launches a fresh recorder with `--max = max(1, min(max_seconds, remaining))`, waits for first growth and plays the Tink. `_join_segments` runs in `_finish_take` right after `_trim_cue`, with 0.25 s of silence at each seam. `[stt] max_take_seconds` (default 1800, bounded 60..7200) and `_MAX_SEGMENTS = 20` are the only new ceilings. `dictate.session` gains no new state word: a paused take still reads `recording`, so the frozen 0.13.0 app needs nothing. Scope is stated out loud: recording pause makes **dictation takes** pausable. `vocalize notes` never records, so a Plaud-style live memo recorder is a separate `vocalize record` command with its own run, not a flag on this one.

**Consequences**: Speech is lost at each seam — the milliseconds before the recorder finalises plus the whole relaunch. The 0.25 s of silence keeps whisper from gluing two half-words together but recovers nothing; manual check 17 measures the gap on the built-in and a Bluetooth input, and that number decides whether pause is usable for memos. Per-segment budgets mean `[stt] max_take_seconds` is now the only thing bounding recorded audio: 1800 s is about 57 MB of WAV in the temporary workdir. `_second_press` must branch on the marker — unpatched, a paused take reaches `_fail`, the workdir is discarded and the user is told the recorder broke. A crash while paused still loses the take; the 24-hour sweep is the backstop.

**Applied to**:
- [design.md](./design.md) § Pause and resume
- [plan.md](./plan.md) § Phase 15b, T-147–T-149
- [verification.md](./verification.md) § Phase 15b exit, manual checks 17 and 18
- [run-15b-recording-pause/project-plan.md](./run-15b-recording-pause/project-plan.md)
