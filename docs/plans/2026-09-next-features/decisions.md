# Decisions: local dictation, readiness status, and the config portal

| # | Question | Status | Decision | Round |
|---|---|---|---|---|
| DEC-001 | What process owns the microphone permission? | Decided | B — a background ad-hoc-signed `.app` bundle built at install | R1 |
| DEC-002 | Which speech-to-text engine and default model? | Decided | A — whisper.cpp via pywhispercpp, default `small.en`; turbo kept as a real option; Apple blocked | R1 |
| DEC-003 | How does dictation coexist with a running read and the playback lock? | Decided | A, modified — stop the read, the playing process records its position, offer to continue after the dictation | R1, refined R2 |
| DEC-004 | How does the portal page authenticate to its server? | Decided | C — one-time `#fragment` code exchanged for an in-memory session token sent in a header | R1 |
| DEC-005 | How are concurrent writers of config.toml kept from clobbering each other? | Decided | A — compare-and-swap; refuse on change | R1 |
| DEC-006 | The `[stt]` config table and CLI command names | Decided | A — as designed | R1 |
| DEC-007 | What happens to audio and transcripts after a dictation? | Decided | A — never stored; clipboard/stdout only; `--cleanup` opt-in sends transcript only | R1 |
| DEC-008 | One release or two? | Decided | B — two releases: 0.10.0 dictation + status, 0.11.0 portal | R1 |
| DEC-009 | Does the "no native app" non-goal still hold? | Decided | Dropped by the owner | R1 |
| DEC-010 | How does `listen --check` measure the microphone grant, and what may it exit with? | Decided | Launch the bundle through LaunchServices and read a status file; the recorder bundle stops carrying the vocalize version; `--check` adds exit 1 for an incomplete local install | R3 |
| DEC-012 | What does the interrupt record do when the stop finds no player, when the replay is stopped, and when the continuation fails? | Decided | A stop always leaves a marker (PID `0` when nothing is playing); the record is only ever replaced, never dropped, while a read is still recoverable | R4 |
| DEC-013 | Whose playback does a stop silence — the player it names, or every read in flight? | Decided | Every read in flight: the marker is left for the window rather than consumed, and any read about to start a piece takes it | R5 |
| DEC-014 | The contract changes the 0.10.0 release review forced | Decided | Claims age from themselves, the clipboard is one line, `--cleanup` is logged by Claude Code, resume carries its voice, and the first press waits out the permission dialog | R5 |
| DEC-015 | What counts toward the portal's five-attempt lockout? | Decided | Every failed `/api/session` exchange, not only a wrong code | R6 |
| DEC-016 | How is a cross-origin POST kept from closing the portal? | Decided | Refused on `Origin` in `route()`, before the lockout counter can see it | R6 |
| DEC-017 | May a run amend its own exit gate, and did run 7's amendment hold? | Decided | Yes when the check cannot pass on any commit; proved red and green before use | R6 |
| DEC-018 | Any local process can close the portal with five `Origin`-less POSTs | Decided — accepted limitation | Accepted, not fixed: availability only, and the attacker who matters can already kill the process. A timed cooldown is the alternative, deliberately not taken | R6 |
| DEC-019 | Run 8's gate names a branch that does not exist, and times out inside its own suite | Decided | Check the isolation criterion's substance instead of the literal name, and raise `CHECK_TIMEOUT` to 600 | R7 |

---

## Round 1

### DEC-001: What process owns the microphone permission?

**Date**: 2026-09-01
**Decided by**: Mat
**Status**: Decided

**Context**: macOS grants the microphone to a responsible process with a usage string. A Services Quick Action runs under `WorkflowServiceRunner.xpc`, which has none, so a bare recorder launched from the hotkey cannot be granted the microphone. Whatever records must have its own identity.

| Option | Description | Trade-offs |
|---|---|---|
| A | Bare Swift CLI recorder | No bundle work; but no usage string and no stable TCC identity — the spike showed a bare CLI records only once permission already exists from some other responsible process |
| B | Background `.app` bundle (Info.plist + one binary), ad-hoc signed, `LSUIElement`, launched with `open -a` | Owns "Vocalize Recorder" in Privacy → Microphone, granted once; built with CLT `swiftc`; identity changes if the binary is rebuilt (mitigated: rebuild only on source-hash change, warn) |
| C | `sounddevice` inside the uv worker | No Swift; but the TCC identity becomes uv's cached Python with no usage string, and PyPI's bundled PortAudio is a recurring Gatekeeper complaint |

**Recommendation**: B.

**Decision**: B. The spike ([spike-2026-09-01.md](./spike-2026-09-01.md) § MIC) confirmed the bundle receives its own permission prompt on first launch (`open -a`, ~150 s stall while the dialog was answered) and records real audio afterwards from both a shell and an Automator-invoked Service.

**Consequences**: A Swift source file and a codesign step enter the repo and the installer; the suite gains a `swiftc -parse` check; a rebuild after a vocalize upgrade may require re-granting the microphone (documented, warned at install). Auto-paste later can live in the same bundle. The recorder must also name or select the input device: the reference Mac's default input was a pair of unworn Bluetooth earbuds delivering digital silence.

**Applied to**:
- [design.md](./design.md) § Structure, § Recorder contract
- [plan.md](./plan.md) § Phase 3 (T-30, T-31)

---

### DEC-002: Which speech-to-text engine and default model?

**Date**: 2026-09-01
**Decided by**: Mat (evidence from the spike; recommendation accepted)
**Status**: Decided

**Context**: The spike measured three whisper.cpp models under `pywhispercpp==1.5.1` on a 30 s synthetic jargon clip (two renderers), plus latency, RSS, swap and cold start on the 8 GB M3, and attempted Apple's on-device recognizer. Full numbers in [spike-2026-09-01.md](./spike-2026-09-01.md).

| Option | Description | Evidence |
|---|---|---|
| A | whisper.cpp, default `small.en` | 30 s clip in 1.3 s (≈24× real time), peak RSS 685–774 MB, jargon 9/12 and 8/12; fixes base's worst misses (`pyproject`, `repository root`, `resolve_provider_settings`) |
| B | whisper.cpp, default `base.en` | 0.5 s, 340 MB, jargon 8/12 and 7/12 |
| C | Apple on-device `SFSpeechRecognizer` | Supported and permission granted, but recognition returns "Siri and Dictation are disabled" — Dictation is off in System Settings on the reference Mac; enabling it is a user settings change. Also needs `NSSpeechRecognitionUsageDescription` in a bundle. Not evaluable now |
| D | whisper.cpp, default `large-v3-turbo-q5_0` | 2.7 s, 647 MB (lower than small), jargon 10/12 and 7/12 — the only model to spell "Kokoro" |

**Recommendation**: A.

**Decision**: A — `small.en` default; `large-v3-turbo-q5_0` kept in the manifest as a real switch (`[stt] model`); `base.en` kept for constrained machines. No resident/pre-warmed worker (per-dictation overhead is ~0.4 s, far under the 2.5 s trigger). The one-time Metal shader compilation (7.9 s) is paid inside `local install --stt`'s selftest, never in a dictation. The owner's real-voice check (verification § Manual 5) can still move the default.

**Consequences**: A 488 MB download on opt-in; three pinned models (hashes verified from completed downloads: base `a03779c8…c6d002`, small `c6138d6d…c41e5d`, turbo `39422170…ffa7e2`); the Apple branch is closed for now and can be reopened only by a user who enables Dictation.

**Applied to**:
- [design.md](./design.md) § Whisper worker protocol, § Contracts (`[stt]` allowlist)
- [plan.md](./plan.md) § Phase 0 (done), § Phase 2 (T-20, T-23)
- [verification.md](./verification.md) § Phase 0 exit

---

### DEC-003: How does dictation coexist with a running read and the playback lock?

**Date**: 2026-09-01
**Decided by**: Mat
**Status**: Decided

**Context**: 0.9.1 serializes playback on a machine-wide flock. Dictation makes sound and records from a microphone that can hear the speakers. Feedback sounds outside `audio.play` would talk over a read and escape `vocalize stop`; recording during a read captures vocalize's own voice.

| Option | Description | Trade-offs |
|---|---|---|
| A | On start: stop playback first; all three sounds through `audio.play` | Simplest; a read in progress is cut |
| B | Refuse to start while a read is playing | Never interrupts; the user must stop it manually |
| C | Sounds via raw `afplay`, no coordination | Reintroduces the overlap 0.9.1 fixed |

**Recommendation**: A.

**Decision**: A, modified by the owner: **stop the read, remember where it stopped, and offer to continue once the dictation is done.** Mechanism refined in round 2 after the review showed the first draft could not work (`play.pid` records no path, and a stopped streaming read deletes its pieces at once): the **playing process** writes the record. `stop_playback(remember=True)` leaves a marker naming the player it is about to stop; the playing `vocalize` process, on seeing its player end by SIGTERM with a fresh marker for it, saves the piece that was playing, the elapsed offset and the text not yet rendered (`interrupted.<ext>`, `interrupted.txt`, `interrupted.json`, 0600). After the transcript lands, a macOS dialog offers "Continue the read?" (default Continue, giving up after 15 s = no); `vocalize resume` does the same from a terminal. Resume plays the saved piece from the offset (`afconvert` → `wave` slice → `audio.play`) and then continues the remaining text through the normal chain with the same provider (cache hits for anything already rendered).

**Consequences**: Edits to the core playback path (`audio.py`, `chain.py`, `cli.py`, `exceptions.py`) with their own tests and review (plan T-46), plus `vocalize resume` (T-47); the record may hold the read's remaining text for up to an hour in the user's cache at 0600 — the same text already sits there as cached audio; no dictation audio or transcript ever enters it (DEC-007). One more dialog in the hotkey flow (skippable in 15 s). Sounds take the playback lock briefly, so they are stoppable and never overlap.

**Applied to**:
- [design.md](./design.md) § Context (Playback), § Key flows (Dictation), § Interrupted-read resume
- [plan.md](./plan.md) § Phase 4 (T-40, T-46, T-47), § Decisions, § Suggested run boundaries
- [verification.md](./verification.md) § Phase 4 exit, § Manual 4

---

### DEC-004: How does the portal page authenticate to its server?

**Date**: 2026-09-01
**Decided by**: Mat
**Status**: Decided

**Context**: The research design asserted "secret in a header, never in a URL" but the browser must learn the secret somehow, and `<audio src>` cannot send headers. The critique traced five findings to this one gap.

| Option | Description | Trade-offs |
|---|---|---|
| A | Token in the opening URL's query string | Lands in browser history, process argv, and `Referer` |
| B | `GET /` serves the page with the token inlined | Anyone who can fetch `/` on loopback gets the token |
| C | Opening URL carries a single-use code in the `#fragment` (never sent to the server); the page exchanges it via `POST /api/session` within 60 s for a session token held in memory and sent as a header on every call; `Host` must equal `127.0.0.1:PORT`; audio previews fetched with the header and played from a Blob; token accepted from the header only | One extra round trip; history holds a dead code; robust against rebinding, CSRF forms, and `<audio>` limitations |

**Recommendation**: C.

**Decision**: C.

**Consequences**: A `POST /api/session` route and a 60 s code lifetime; every mutating route gets a token-in-query negative test; the page cannot be deep-linked (fine).

**Applied to**:
- [design.md](./design.md) § Portal auth, § Portal routes
- [plan.md](./plan.md) § Phase 6 (T-60)
- [verification.md](./verification.md) § Phase 6–7 exit

---

### DEC-005: How are concurrent writers of config.toml kept from clobbering each other?

**Date**: 2026-09-01
**Decided by**: Mat
**Status**: Decided

**Context**: The portal, the wizard, `vocalize chain`, and a hand edit can all write the file; each reads at its own start and writes a full merged dict later.

| Option | Description | Trade-offs |
|---|---|---|
| A | Compare-and-swap on the file's mtime + sha256 from when it was read; refuse with "config changed on disk — reload" on mismatch | Small, stdlib, no lock file |
| B | Advisory flock around read-modify-write | Cannot help a page that read minutes ago |
| C | Nothing | Data loss in a realistic case |

**Recommendation**: A.

**Decision**: A, via a `write_config_if_unchanged(path, data, fingerprint)` helper in `wizard.py` used by the portal, `vocalize chain`, and the wizard.

**Consequences**: `vocalize chain` and the wizard re-read before writing (a behavior change only when the file moved underneath them); the portal surfaces a reload prompt.

**Applied to**:
- [design.md](./design.md) § Portal routes
- [plan.md](./plan.md) § Phase 6 (T-62)
- [verification.md](./verification.md) § Phase 6–7 exit

---

### DEC-006: The `[stt]` config table and CLI command names

**Date**: 2026-09-01
**Decided by**: Mat
**Status**: Decided

**Context**: Public contract once shipped.

| Option | Description | Trade-offs |
|---|---|---|
| A | `[stt]` table (model, language, cleanup, paste, max_seconds, sounds, input_device); commands `vocalize listen` (`--toggle`, `--check`, `--wav`, `--cancel`, `--cleanup`), `vocalize dictate`, `vocalize local install --stt`, `vocalize resume` | Mirrors `[providers.*]`; `listen` composes with pipes |
| B | Treat STT as a provider table | Wrong shape; leaks into `resolve_chain` |
| C | Only `vocalize dictate` | Loses the pipe and the test seam |

**Recommendation**: A.

**Decision**: A (with `input_device` added after the spike's silent-default-device finding, and `vocalize resume` added by DEC-003).

**Consequences**: `vocalize settings` grows `stt.*` lines; `hooks/speak_options.py` keeps ignoring unknown lines (pinned by test).

**Applied to**:
- [design.md](./design.md) § Contracts
- [plan.md](./plan.md) § Phase 4 (T-41, T-42)

---

### DEC-007: What happens to audio and transcripts after a dictation?

**Date**: 2026-09-01
**Decided by**: Mat
**Status**: Decided

**Context**: The recording is the user's voice; the transcript may be anything they said.

| Option | Description | Trade-offs |
|---|---|---|
| A | Audio only in a 0700 temp dir deleted on every exit path (+ a > 24 h stale sweep); transcript to the clipboard (and stdout for `listen`), never written to disk or logged; notifications carry fixed text; `--cleanup` sends the transcript (never audio) to `claude -p`, off by default | Nothing to leak later; no history |
| B | A captures history like voicebox | Convenient; a store of everything said |

**Recommendation**: A.

**Decision**: A.

**Consequences**: No transcript history feature; a mishear is visible only after pasting (accepted trade-off, stated in the docs).

**Applied to**:
- [design.md](./design.md) § Key flows, § Testing strategy
- [plan.md](./plan.md) § Phase 4 (T-40, T-43)

---

### DEC-008: One release or two?

**Date**: 2026-09-01
**Decided by**: Mat
**Status**: Decided

**Context**: Dictation and the portal are independent surfaces with different review needs.

| Option | Description | Trade-offs |
|---|---|---|
| A | One release | One merge; a very large review |
| B | 0.10.0 = dictation + `status`; 0.11.0 = portal | Two smaller reviews; `status` de-risks the portal's aggregation |

**Recommendation**: B.

**Decision**: B.

**Consequences**: Two adversarial reviews, two publishes; the portal branch starts from the merged 0.10.0.

**Applied to**:
- [plan.md](./plan.md) § Phases 5 and 8
- [design.md](./design.md) § Approach

---

### DEC-009: Does the "no native app" non-goal still hold?

**Date**: 2026-09-01
**Decided by**: Mat
**Status**: Decided

**Context**: The planning request listed "native app" as a non-goal; the critique flagged that the recorder bundle strains it.

| Option | Description | Trade-offs |
|---|---|---|
| A | Keep it | Forces the uv/PortAudio recording route with a worse permission story |
| B | Drop it | Unblocks DEC-001 B; a menu-bar agent may be proposed later without a scope argument |

**Recommendation**: B.

**Decision**: Dropped — "that was before we expanded scope" (owner).

**Consequences**: DEC-001 B proceeds.

**Applied to**:
- [design.md](./design.md) § Approach
- [plan.md](./plan.md) § Scope

---

## Round 2

Applying round 1 surfaced no new one-way doors. Two-way details that changed are listed in [plan.md](./plan.md) § Decisions (input device selection, WAV format assertion, Metal warm-up in the selftest, resume dialog timing).

## Review round (2026-09-02)

Three independent reviewers (security with untrusted-input tracing, operations on the 8 GB reference Mac, executability) read the document set as it stood after round 1. Dispositions; none opened a one-way door, so no entry needed the owner, but the two starred rows are worth the owner's glance.

| Finding (lens) | Severity | Disposition | Lands in |
|---|---|---|---|
| Resume cannot work as drafted: `play.pid` has no path and the stopped process deletes its pieces (all three lenses) | Critical | Accepted; redesigned as a player-side record ★ | DEC-003 (refined), design § Interrupted-read resume, T-46, T-47 |
| Readiness timeout unproven against a blocking native keychain call; the portal's polling would leak threads (security, ops, executability) | High | Accepted: daemon threads, one in-flight probe per name, uninterruptible fake in the test | design § Readiness aggregation, T-10, T-12, T-61, verification Phase 1 |
| `rec.pid` trusted without liveness or process-name check; two presses can race (security, ops) | High | Accepted: recorder writes its own PID; name check before any signal; `O_EXCL` session claim | design § Key flows, § Recorder contract, T-30, T-40 |
| `install.py` generalization does not cover the selftest argv and the all-files stamp (executability) | High | Accepted: per-manifest `selftest_argv`, `files=` subset | design § Installer generalization, T-21, T-23 |
| Mermaid graph omitted T-05, T-46 and the T-21→T-63 edge (executability) | High | Accepted | plan § Dependencies |
| Portal preview plays outside the machine-wide playback lock (ops) | High | Accepted as a stated exception ★ — the browser cannot take the file lock and a preview is seconds long; the CLI read keeps playing and `vocalize stop` is unaffected | design § Context, § Portal routes, plan § Decisions, manual check 4a |
| Preview bypasses budget gate, ledger and cache; Kokoro's global session races under the threaded server (security, executability) | Medium | Accepted: preview goes through `chain.run` with the provider forced, under one module lock | design § Portal routes, T-63 |
| `Host` check ambiguous for `/` and `/api/session`; code entropy and brute force unspecified (security) | Medium | Accepted: `Host` on every request; `secrets.token_urlsafe(32)`; five wrong codes end the server | design § Portal auth, T-60 |
| `input_device` reaches the recorder's argv unvalidated (security) | Medium | Accepted: shape check | design § `[stt]`, T-42 |
| Cleanup pass has no timeout and no stated prompt or injection test (security) | Medium | Accepted: 120 s timeout, prompt text in the design, injection test | design § Cleanup pass, T-43 |
| Interrupt record not permission-hardened (security) | Medium | Accepted: 0600 | design § Interrupted-read resume, T-46 |
| DEC-005 has no fingerprint for a missing file (executability) | Medium | Accepted: `"absent"` sentinel + `O_EXCL` first write | design § Portal routes, T-62 |
| No diagnosis for a missing `swiftc` or an unaccepted CLT license (ops) | Medium | Accepted | T-31, verification Phase 3 |
| No way to reclaim up to 1.2 GB of STT models (ops) | Medium | Accepted: `local uninstall --stt`, sizes in `local status` | design § CLI, T-25 |
| Memory verdict never included a browser (ops) | Medium | Accepted as a manual check before each release | verification Manual 0 |
| Recorder exit 3 overloaded between modes (executability) | Low | Accepted: not-determined is exit 5 | design § Recorder contract, T-30, T-32 |
| `readiness()` signature differs between documents; `max_seconds` clamps where siblings raise (executability) | Low | Accepted: keyword-only in both; `ConfigError` outside 1–600 | T-10, T-42 |
| CSP lacks `media-src` for Blob audio (executability) | Low | Accepted | design § Portal routes, verification Phase 6–7 |
| Phase 0/5/8 exit rows were intentions, not commands (executability) | Low | Accepted: grep-based checks; the review writes a findings table with a Status column | verification § Phase 0, 5, 6–7 |
| No portal route for `[stt]` (executability) | Low | Accepted: Local tab + `POST /api/stt` | design § Portal routes, T-62, T-70 |
| Plan too large for one run (executability) | Process | Accepted: suggested cut points recorded for split-plan | plan § Suggested run boundaries |
| Effort under-budgeted for resume (ops) | Low | Accepted: 0.10.0 ≈ 42 h, 0.11.0 ≈ 62 h | plan § Decisions |

Rejected: none.

The re-read after folding (one independent reviewer, changed sections only, checked against the real code) found six more, all accepted:

| Finding | Severity | Disposition | Lands in |
|---|---|---|---|
| Preview called `chain.run` with a `play=` parameter that does not exist and without the required `file_config` | Critical | Fixed: real signature; `run` returns `(audio, name, ext)` and never plays | design § Portal routes, T-63 |
| The record conflated `PlaybackStopped.audio` (every piece so far, joined) with the current piece; an offset into the joined blob is wrong | Critical | Fixed: the record copies `last_stop().path`, the player's own copy of the piece that was playing | design § Interrupted-read resume, T-46 |
| `chain._synthesize_with` does not exist | High | Fixed: `chain._speak` | design, T-46 |
| A 10 s marker window consumed at the except site races synthesis latency (a slow cloud chunk drops the record silently) | High | Fixed: the marker is consumed on the playing thread the moment the player exits, so the window covers only stopper-to-exit latency; slow-chunk test added | design step 2, T-46, verification Phase 4 |
| The Phase 1 verification command registered a probe under a name that chain validation might reject | Medium | Fixed: `_PROBES` is a plain name→callable seam; the command uses a real chain | design § Readiness aggregation, verification Phase 1 |
| A recorder that dies before writing `rec.pid` was relaunched silently — a revoked microphone becomes a retry loop | Medium | Fixed: dead recorder → clear session, Pop, fixed-text notification naming `listen --check`, exit 1, never a relaunch | design § Key flows, T-40 |

---

## Round 3 (2026-09-02, run 3 review)

### DEC-010: How does `listen --check` measure the microphone grant, and what may it exit with?

**Date**: 2026-09-02
**Decided by**: run 3 executor, on three independent reviews of the shipped run-3 code
**Status**: Decided

**Context**: The reviews found the command that exists to answer "does the recorder have the microphone?" was answering about the wrong process, and that four smaller choices around the bundle's identity and exit codes contradicted either the design or each other.

| # | Question | Options | Decision |
|---|---|---|---|
| a | Which process does `--check` measure? | Direct exec of the bundle's binary (fast, simple) vs `open -W -n -a <bundle>` + a status file (the launch path dictation uses) | **LaunchServices.** TCC answers for the *responsible* process. Proven live on the reference Mac: the same binary in the same second reported `authorized` exec'd from the terminal and `notDetermined` launched through `open`. The direct exec was measuring Claude Code's grant. `open` relays neither stdout nor the child's exit status, hence `--status-file`. `--list-devices` needs no permission and stays a direct exec |
| b | Does the bundle carry the vocalize version? | Substitute `__VERSION__` into `Info.plist` and fingerprint it vs a fixed `1.0` | **Fixed.** The ad-hoc signature is the TCC identity, so a version in the plist made every release rebuild a byte-identical recorder and silently revoke dictation until the user re-approved it. The fingerprint is now source + plist + stamp version, plus the built binary's own sha256 |
| c | What may `listen --check` exit with? | Keep 0/2/3/5 only and map "not built" onto one of them vs add 1 | **Add 1**, meaning "vocalize's own local install is not finished" — not built, no model, or a recorder that did not report back. Anything else the recorder returns is clamped to 1, so a signal-killed recorder cannot exit 245 and the set is closed. verification.md § Phase 3 and this run's validate-exit.sh were amended to match |
| d | `LSBackgroundOnly` in the bundle plist | Keep (plan T-30 named it) vs drop | **Drop.** `LSUIElement` alone already gives no Dock icon and no focus stealing. `LSBackgroundOnly` is the stronger, Apple-discouraged key declaring an app that can never come to the foreground — an unverified risk on the one bundle whose entire job is to be the identity a microphone prompt names |
| e | Hardened runtime on an ad-hoc signature | `codesign -s -` vs `codesign -s - --options runtime` | **Hardened.** The recorder is the only process on the machine holding a microphone grant; without the hardened runtime it honours `DYLD_INSERT_LIBRARIES`, so anything running as the user could record under its grant with no prompt of its own. It links only system frameworks, so library validation costs nothing |

**Consequences**: `RECORDER_STAMP_VERSION` goes to 2, so every existing bundle is rebuilt once — and that rebuild does change the signature, so the install prints the re-grant warning. After it, an upgrade no longer does. Run 4 must launch the recorder with `open` for recording as well, and must treat `listen --check` exit 1 as "tell the user to finish the install", not as a crash.

**Applied to**:
- [design.md](./design.md) § Recorder contract, § Terminal primitive
- [verification.md](./verification.md) § Phase 3
- `vocalize/recorder/VocalizeRecorder.swift`, `vocalize/recorder/Info.plist.in`, `vocalize/local/install.py`, `vocalize/cli.py`
- [run-3-recorder/validate-exit.sh](./run-3-recorder/validate-exit.sh), [run-3-recorder/project-plan.md](./run-3-recorder/project-plan.md)

### DEC-011: What happens when a dictation is interrupted, killed or lied to?

**Date**: 2026-09-02
**Decided by**: run 4 executor, on three independent reviews of the shipped run-4 code
**Status**: Decided

**Context**: The reviews found five ways the toggle could be left holding something it should not — the same take twice, a claim nobody was behind, a session file no command could clear, an open microphone, or a directory it was told to delete. Each is a state machine question, not a bug in one branch, so they are decided together.

| # | Question | Options | Decision |
|---|---|---|---|
| a | When is a take claimed? | When the transcription starts (`_finish_take`) vs the first statement of a stop or a cancel | **First statement.** Between "the user asked to stop" and "the transcription started" sit the wait for the recorder (up to `_STOP_TIMEOUT`) and a feedback sound that queues on the machine-wide playback lock. A press in that window found no claim, a recorder that had already exited and a finished WAV — and ran the whole stop again: two `uv run` workers on the same take, two clipboard writes, and whichever `finally` fired first deleting the directory under the other |
| b | What makes a claim stale? | Its existence is the claim vs the claim carries the claiming PID and expires | **PID plus an expiry** (`_FINISH_TIMEOUT` = stop + transcribe + cleanup timeouts). A process killed mid-transcription otherwise left a claim nobody was behind, and every later press was refused with "Still transcribing the last dictation" for ever. The sweep the module docstring names cannot help: it runs only *after* a claim succeeds |
| c | May `listen --cancel` refuse? | Refuse while transcribing, as a press does vs never refuse | **Never.** Every message about a stuck dictation names `--cancel` as the way out, so it cannot answer "still transcribing" — that was a closed loop with no exit. A live transcription keeps its own directory (it owns the take and removes it in its own `finally`); only the session is released |
| d | What does a session file nobody can read mean? | Retry, then return 0 in silence vs clear it and say so | **Clear it.** A press killed between the `O_EXCL` create and its JSON left a zero-byte file that failed every later claim, and nothing swept it. Same for a `dir` that is not one of this module's own `mkdtemp` directories directly under the system temporary directory: the session file is state on disk, so its `dir` is untrusted input — a planted one would have turned the next press into a recursive delete of whatever it named |
| e | When may a stop signal the recorder? | Only after `--max` + `_BACKSTOP_GRACE` vs also `_STOP_TIMEOUT` after the stop file was written | **Both, whichever comes first.** With the default `max_seconds = 120` the backstop sat 125 s away while the wait gave up at 20 s, so a stop early in a recording could never reach it: the microphone stayed open for the rest of the two minutes. The signal is still the only one this module sends and still only to a PID whose process name is the recorder. A cancel with no PID yet now watches for a late `rec.pid` for the rest of `_START_GRACE` instead of deleting the directory its stop file lives in and walking away |
| f | What does a press report when another press already resolved it? | Always report what it saw vs stay silent when the session is no longer ours | **Silent, exit 0.** A first press still waiting for `rec.pid` when the second press cancelled reported "The recorder did not start" on top of "Dictation cancelled", and sent the user to a diagnostic command for a fault that never happened |

**Consequences**: `_mark_finishing` / `_finish_claim` replace the bare `transcribing` marker; `_terminate` is the one place a signal is sent, shared by the stop wait and the late-starter watch; `_second_press` and `cancel` both route an unreadable session through `_clear_wedged_session`. The refuse-while-transcribing behaviour of the *hotkey* is unchanged (design § Key flows). `listen --cancel` against a live transcription now returns 0 having released the session, where it used to return 0 having refused.

**Applied to**:
- [design.md](./design.md) § Key flows
- `vocalize/dictate.py`, `tests/test_dictate.py`
- [run-4-dictation/report.md](./run-4-dictation/report.md)

---

## Round 4

### DEC-012: What does the interrupt record do when the stop finds no player, when the replay is stopped, and when the continuation fails?

**Date**: 2026-09-02
**Decided by**: run 5 executor, on three independent reviews of the shipped run-5 code
**Status**: Decided

**Context**: DEC-003 settled *who* records an interrupted read. The reviews found four states it does not cover, all of which end with a read the user asked to keep and cannot get back — plus one privacy claim in the design that is simply not true.

| # | Question | Options | Decision |
|---|---|---|---|
| a | What does a remembered stop do when there is no player to name? | Return False and write nothing vs write a marker naming no player | **Write it, naming PID `0`.** A streamed read is playing nothing for as long as the next chunk takes to render — tens of seconds on a cloud provider. A stop in that gap found no PID file, so nothing was killed, nothing was recorded, and the queued piece then played into the open microphone. `cli._StreamPlayer._drain` now takes such a marker before it starts a piece, and only one written after its own read began: a dictation that stopped nothing leaves the same marker behind, and a read started afterwards must not stop itself on it |
| b | Is a stale marker naming another player swept? | Leave it (it is not ours) vs remove anything past the window | **Remove it.** A player SIGKILLed before it could look leaves a marker no code path would ever collect. Freshness already gated the record, so this is tidiness rather than a fix — but it is what T-46's acceptance criterion asks for. A *fresh* foreign marker is still left exactly where it is |
| c | A dictation stops the resume's replay. Then what? | Forget the record, as any stop of a replay did vs re-record from where the replay stopped | **Re-record.** The replay goes through `audio.play`, not `_run_tts`, so nothing was writing a replacement: the record was deleted and the rest of the read was gone for good — while a stop during the *continuation* was re-recorded, so the two halves of one resumed read behaved oppositely. The saved slice becomes the new piece, the text is unchanged, and `remember_stop`'s own gate keeps a plain `vocalize stop` on today's forget-and-drop |
| d | When is the record deleted on a successful resume? | Before the continuation vs after, unless something replaced it | **After, and only if it is still the same record.** `forget()` before `_run_tts` meant a continuation that raised before it spoke — budget spent, keychain locked, forced provider offline — destroyed a read the user had just asked to continue, with nothing left to retry. Comparing `saved_at` keeps the original behaviour where it mattered: a stop during the continuation writes a newer record, and that one is never deleted by this one |
| e | Is `interrupted.txt` new plaintext on disk? | Keep the design's "the text was already on disk as cached audio" vs say plainly that it is new | **Say it plainly.** The audio cache holds audio; this is the first time a read's *text* is written to disk. The mitigations are unchanged and are the whole answer — 0600, `O_NOFOLLOW`, one record at a time, deleted on resume, on decline and after an hour — but the justification was wrong, and a reader trusting it would under-rate the file |

**Consequences**: `audio.stop_playback` always leaves a marker under `remember=True`, so `_write_interrupt_request` is no longer conditional on a live player; `audio.take_gap_stop` and `audio.stop_found_no_player` are new; `take_interrupt_request` reads the marker `O_NOFOLLOW` and sweeps anything past the window. `cli.resume_interrupted` re-records a remembered stop of the replay and defers `forget()` to after the continuation. The dialog waits up to 3 s for a record a slow chunk has not written yet, and only when its own stop is known to have hit something.

**Applied to**:
- [design.md](./design.md) § Interrupted-read resume
- `vocalize/audio.py`, `vocalize/cli.py`, `vocalize/dictate.py`, `vocalize/interrupted.py`
- `tests/test_audio.py`, `tests/test_cli.py`, `tests/test_dictate.py`
- [run-5-resume/report.md](./run-5-resume/report.md)

---

## Round 5

The adversarial review of the shipped 0.10.0 branch ([review-0.10.0.md](./review-0.10.0.md), T-51). Two decisions came out of it: one about who a stop silences, and one covering every other contract the fixes moved.

### DEC-013: Whose playback does a stop silence?

**Date**: 2026-09-02
**Decided by**: run 6 executor, on the 0.10.0 adversarial review
**Status**: Decided

**Context**: `audio.py`'s own docstring names the case the review broke it on — "a /speak issued while another read is going, two Claude Code sessions finishing at once". Read A holds the machine-wide playback lock; read B is blocked on it. A dictation calls `stop_playback(remember=True)` once, which kills A's player. A's `play_sequence` returns False and releases the lock, and B immediately starts speaking — into a microphone the recorder is still opening. DEC-012(a) fixed the *gap* between two pieces of one read; it did not fix a second read behind the first. Two more states fall out of the same design: a plain `vocalize stop` in a gap is ignored entirely (`take_gap_stop` only ever consumed a marker `remember=True` wrote), and a non-streaming provider still inside `synthesize()` has no player to kill, so it plays a full read seconds after the stop.

| Option | Description | Trade-offs |
|---|---|---|
| A | A held "dictation active" claim, checked in `audio._play_now` | Covers every case including a read that has not launched a player yet; but `_play_now` is also how dictation plays its own Tink and Pop, so a dictation would silence its own feedback — and the claim needs a lifecycle across `_start`/`_stop`/`_cancel`/`_fail` |
| B | Leave the stop marker in place for its window instead of consuming it, and let any read about to start a piece take it | No new file, no new lifecycle, and a *smaller* module: `take_gap_stop` stops being about gaps and becomes the one question every read asks before it makes a sound. Feedback sounds go through `audio.play` and never ask, so they are unaffected. The `since` argument keeps a read started *after* the stop from silencing itself |
| C | Narrow the documented guarantee to "audio already playing" | Free, and honest; but the promise dictation is built on is that the microphone does not hear vocalize, and B is exactly the case where it does |

**Recommendation**: B.

**Decision**: B. `take_interrupt_request` and `take_gap_stop` no longer unlink the marker; it expires on `INTERRUPT_WINDOW` as it always did. `take_gap_stop` accepts a marker naming any PID, not only `_NO_PLAYER`, and `stop_playback()` writes one whether or not `remember` was asked for — with the remembered flag on its own line, so a plain stop still records nothing (`interrupted.remember_stop` already gates on it). `cli._run_tts` asks the same question before the non-streaming `play_audio(dest)`.

**Consequences**: Within one `INTERRUPT_WINDOW` (10 s) of a stop, every read that was already in flight is silenced, not just the one whose player was killed. A read *started* after the marker is unaffected, because every consumer compares the marker's timestamp against its own start. Two reads interrupted together both try to write the interrupt record and the later one wins — the record has one slot, and both are recoverable reads; before this change the second was not stopped at all. `vocalize stop` between two streamed pieces now stops the read, which is what the command has always claimed to do.

**Applied to**:
- [design.md](./design.md) § Interrupted-read resume
- `vocalize/audio.py`, `vocalize/cli.py`
- `tests/test_audio.py`, `tests/test_cli.py`

### DEC-014: The contract changes the 0.10.0 release review forced

**Date**: 2026-09-02
**Decided by**: run 6 executor, on the 0.10.0 adversarial review
**Status**: Decided

**Context**: The review returned findings whose fixes each move something a future reader would otherwise relitigate against the shipped design. They are recorded together; the mechanical fixes (a bare `ps` on the dictation path, an unguarded `mkdir`, a lock taken too late in `readiness()`) changed no contract and are not here.

| # | Question | Options | Decision |
|---|---|---|---|
| a | Does `[stt] cleanup` still satisfy "no transcript is ever written to a file"? | Keep the claim vs contain the log vs state it | **State it.** Claude Code persists the prompt and stdin of every print-mode run to `~/.claude/projects/<slug>/<uuid>.jsonl`. Containment was tried: `CLAUDE_CONFIG_DIR` does move the log, but the run then fails with "Not logged in", so the feature stops working. Deleting Claude Code's session file afterwards was rejected — a TTS tool reaching into another program's history to delete files it did not write is worse than the honest sentence. DEC-007's "never stored" is now scoped to vocalize's own files, and `--cleanup` carries the exception in README, docs/dictation.md and the module docstring |
| b | What working directory does the cleanup pass run in? | The caller's vs the system temporary directory | **The temporary directory**, plus `--strict-mcp-config`. Claude Code adopts its cwd as the *project*: run from a repository, the one session fed microphone-captured text was loading that project's `CLAUDE.md`, `.claude/settings.json` (permissions and hooks) and its MCP servers. The transcriber subprocess fifty lines above already does this for a weaker reason |
| c | What does a claim on a take age from? | The session's `started` vs the claim file's own mtime | **The claim's mtime**, refreshed as the stop passes each stage. `_FINISH_TIMEOUT` was documented as "everything a stop can be waiting on, added up" and was not: it omitted the recording itself (`max_seconds` is up to 600 s) and the unbounded wait for the machine-wide playback lock inside `_stop`. A ten-minute dictation therefore read as a dead claim the moment transcription began, and the next press `rmtree`'d the working directory out from under it. The claim also gains the claiming process's `ps -o comm=` name, so a recycled PID cannot hold the hotkey for a whole stage |
| d | Which temporary roots may a session's `dir` live under? | Only this process's `gettempdir()` vs the plausible roots, with ownership checks | **The plausible roots** — `gettempdir()`, `/tmp`, `/private/tmp` — and the directory must be a directory, owned by this uid, mode 0700. The narrow check was not a security boundary, it was a mismatch: the hotkey records a `/var/folders/…/T/` path and `listen --cancel` from an ssh login or a launchd job sees `/tmp`, so it cleared the session and left the recorder holding the microphone. The added ownership and mode checks make the widened test stricter than the one it replaces |
| e | Does the clipboard get the transcript's newlines? | Keep them, as `sanitize` does vs collapse them | **Collapse them.** A multi-line paste into a terminal without bracketed-paste protection runs each line as it arrives. `--cleanup`'s model output and a `--wav` transcript can both contain newlines. Dictation produces sentences; `vocalize listen`'s stdout still keeps them |
| f | What does an interrupted read remember about how it was being spoken? | Provider, extension and offset vs those plus voice, model, speed and chunking | **All of them.** A read stopped mid-way was continued in the *config default* voice at default speed, and because the cache keys on resolved settings, every remaining chunk was a miss: the rest of the document was re-synthesized and re-billed. `interrupted.json` goes to version 2 (a version-1 record is discarded on upgrade, as any unreadable one is) and the four values are shape-checked on the way out like `ext` and `provider` already are |
| g | May `load()` delete a record that is mid-save? | Any unreadable record is forgotten vs a missing JSON is "no record yet" | **A missing JSON is no record.** `save()` writes audio, then text, then JSON last precisely so a half-written record has no JSON — but `load()` treated the resulting `FileNotFoundError` as corruption and `forget()` deleted the audio and text the saver had just written. `dictate` polls `load()` every 50 ms for exactly the three seconds that write takes. `forget()` now fires only for a JSON that exists and is unusable |
| h | What does `vocalize status` say about a microphone verdict it did not measure? | The word alone vs the word and its age | **Both.** `mic.status` gains the time it was written; a verdict older than a day is reported as `warn` with its age and the command that refreshes it. Measuring for real means launching the bundle through LaunchServices (DEC-010), which a status screen must not do — so the staleness is made visible instead of removed |
| i | Which device does `listen --check` measure? | The system default vs the configured `[stt] input_device` | **The configured one.** `input_device` exists because the reference Mac's default input was a pair of unworn earbuds; `--check` was reporting on those and saying "ready" while the configured device was not connected. An empty value still means the system default and is passed through unchanged |
| j | What happens on the first hotkey press after an install, while macOS asks for the microphone? | Fail after `_START_GRACE` vs wait while the dialog is up | **Wait.** The spike measured ~150 s for that dialog to be answered; `_START_GRACE` is 5 s. Every machine's first dictation therefore failed, deleted its own working directory, and told the user to run a diagnostic that then reported "authorized". The recorder now writes a `rec.prompt` marker the instant it enters `requestMicrophoneAccess` and removes it when the answer comes; `_launch_recorder` waits while that marker exists, up to `_PROMPT_GRACE`. Requesting the grant during `local install --stt` instead was rejected for this release: it puts a permission dialog in the middle of a non-interactive `--yes` install |
| k | Does a dictation that cannot start still kill the read? | Stop first, then find out vs validate first | **Validate first.** `_checked(stt)` and `_recorder_bundle()` are pure checks; running them before `stop_playback` means a fresh install or a hand-edited `[stt]` value no longer silences a read for nothing. And `_after_stop` now offers to continue the interrupted read on *every* outcome — the read is a separate thing from whether a transcript landed |

**Consequences**: `interrupted.json` version 2 discards any record written by 0.10.0's version 1 — at most one read, at most an hour old. `mic.status` gains a second line; a file written by 0.10.0 reads as "measured at an unknown time" rather than failing. The first press after a fresh install now blocks for as long as the user takes to answer the dialog, instead of failing in five seconds. Everything else is invisible to a working install.

**Applied to**:
- [design.md](./design.md) § Key flows, § Interrupted-read resume, § Input device, § Terminal primitive
- [verification.md](./verification.md) Phase 5, Phase 6-7, Manual 4b
- `vocalize/dictate.py`, `vocalize/interrupted.py`, `vocalize/readiness.py`, `vocalize/cli.py`, `vocalize/audio.py`, `vocalize/local/install.py`, `vocalize/recorder/VocalizeRecorder.swift`
- `README.md`, `docs/dictation.md`
- [review-0.10.0.md](./review-0.10.0.md)

---

## Round 6

Run 7 shipped the portal server. Three choices it made were argued only in code
comments and in [run-7's report](./run-7-portal-read/report.md); the adversarial
review ([review-findings.md](./run-7-portal-read/review-findings.md), finding 14)
called the first of them out by name. Recorded here because run 9 writes the page
that has to live with DEC-015 and DEC-016. DEC-018 was added by a later fix round:
it decides nothing new, it writes down the limitation the first two leave behind.

### DEC-015: What counts toward the portal's five-attempt lockout?

**Date**: 2026-09-03
**Decided by**: run 7 executor, ratified against the adversarial review
**Status**: Decided

**Context**: design § Portal auth scoped the shutdown to "five wrong codes". `POST
/api/session` can fail four other ways: the code has already been used, it has
expired, the body is not JSON, and the body is JSON with no `code` in it. A replay
of a used code is the awkward one — the server keeps no record of who used it, so a
legitimate page reload and an attacker replaying a code it sniffed are the same
request byte for byte.

| Option | Description | Trade-offs |
|---|---|---|
| A | Count only a wrong `code` value | Matches the design's words; but the server would have to tell a replay from an attack to do it, and it deliberately keeps nothing that can — so the counter becomes trivially bypassable by replaying a used code instead of guessing a new one |
| B | Count every failed exchange | One counter, no state to distinguish anything, nothing to bypass; but a real user who reloads the tab five times closes their own portal |
| C | Keep per-reason counters | Preserves the design's wording and the user's reloads; but it is more state on the auth path than the whole exchange has, and every extra bucket is another thing that has to be right |

**Recommendation**: B.

**Decision**: B. `_refuse_code` increments on every failure path, and the fifth
calls `lock_out()`. The response carries `attempts_left` so a page can see it coming.

**Consequences**: Reloading the portal page five times closes the portal; the
recovery is to run `vocalize portal` again, which mints a fresh code. **Run 9's page
must strip the `#code=` fragment as soon as it has exchanged it, and must never retry
the exchange on its own** — an automatic retry loop would burn all five attempts
without a person doing anything. This decision is what makes DEC-016 necessary: a
counter that fires on any failure is a counter anyone who can send a request can
fire, so cross-origin requests had to stop reaching it.

**Applied to**:
- `vocalize/portal.py` (`_refuse_code`), `tests/test_portal.py`
- [run-7-portal-read/report.md](./run-7-portal-read/report.md) § Deviations 4
- [design.md](./design.md) § Portal routes

---

### DEC-016: How is a cross-origin POST kept from closing the portal?

**Date**: 2026-09-03
**Decided by**: run 7 executor, on the adversarial review's finding 1
**Status**: Decided

**Context**: With DEC-015 in force, any page in any tab could shut the user's portal
down. `POST /api/session` needs no token, and a simple cross-origin form POST needs
no CORS preflight and does not need to read the answer — five of them and the portal
is gone. `Host` does not stop it: the browser sends the real `Host` for
`127.0.0.1:<port>`. The one thing that separates a hostile page's POST from our own
page's is that both carry `Origin`, and only ours carries ours.

| Option | Description | Trade-offs |
|---|---|---|
| A | Require a correct `Origin` on every request | Strictest; but `curl` and any non-browser client send no `Origin` at all, and the portal's own smoke tests are `curl` |
| B | Refuse a *present and wrong* `Origin`, before the lockout counter | Kills the attack at the only point where it is distinguishable, leaves `curl` (no `Origin`) working, and costs two lines |
| C | Leave it, and narrow the lockout instead | Undoes DEC-015 and puts the replay-vs-attack problem back |

**Recommendation**: B.

**Decision**: B. `route()` refuses a request whose `Origin` is present and is not
`http://<host>:<port>` with a 403, immediately after the `Host` pin and **before**
`_refuse_code` can count it. An absent `Origin` is allowed through, because that is
what every non-browser client sends and a browser cannot forge an absent one on a
cross-origin POST.

**Consequences**: A hostile page's POST is refused and costs the user nothing. A
CORS preflight also fails, which is the intended answer — the portal has no
cross-origin callers. The check is not itself the authentication: it is the guard
that keeps DEC-015's counter out of a stranger's reach, and the token gate is
unchanged behind it. Run 9's page is same-origin, so its own `Origin` matches and it
is unaffected.

**Applied to**:
- `vocalize/portal.py` (`route()`, `_refuse_code`'s docstring), `tests/test_portal.py`
- [run-7-portal-read/report.md](./run-7-portal-read/report.md) § Deviations 4
- [design.md](./design.md) § Portal routes

---

### DEC-017: May a run amend its own exit gate, and did run 7's amendment hold?

**Date**: 2026-09-03
**Decided by**: run 7 executor, flagged for the owner in the handoff report
**Status**: Decided

**Context**: Run 7's `validate-exit.sh` entry check `0.10.0 shipped (version on main
≥ 0.10.0)` read `main:pyproject.toml` for a `version = "…"` line. This project
declares `dynamic = ["version"]` and `[tool.hatch.version]` points at
`vocalize/__init__.py`, so that line has never existed on any commit in this
repository. The check failed with main at 0.10.1 exactly as it had with main at
0.9.1. A run may not normally edit its own gate — that is the owner's lever, and a
run that can move its own bar has no bar.

| Option | Description | Trade-offs |
|---|---|---|
| A | Leave it failing and hand over 10 / 11 | Honest, and touches nothing; but the run then ships with a red gate whose redness says nothing about the run, and every later run inherits the same dead check |
| B | Delete the check | Smallest diff; but it drops a real entry criterion — 0.11.0's portal genuinely does depend on 0.10.0 having shipped |
| C | Point it at the file hatch reads, and prove it both ways | Restores the criterion the check was written to enforce; but it is a run editing its own gate, which needs to be visible rather than quiet |

**Recommendation**: C.

**Decision**: C, with the amendment declared in the handoff report rather than made
silently. The check now reads `main:vocalize/__init__.py` for `__version__`. It was
proved green on the real `0.10.1`, red on a synthetic `0.9.1` and red on an empty
file before it was used, so it is not a vacuous pass. Commit `e53c48c`, with the
reasoning in a comment above the check itself.

**Consequences**: The amendment is legitimate on a narrow ground and only that one:
the original check could not pass on *any* commit, so it tested nothing and lowered
no bar when it was replaced — a gate that cannot pass proves as little as one that
cannot fail. Changing a check that *can* fail, on a branch where it is failing,
would not be. It also brings run 7's gate into line with the only two siblings that
check a version at all — run 6's and run 10's both already read
`grep '^__version__' vocalize/__init__.py` — which is corroboration that
`main:pyproject.toml` was an authoring slip in this one script and not a different
reading of where the version lives. No other run's `validate-exit.sh` was touched.

**Applied to**:
- [run-7-portal-read/validate-exit.sh](./run-7-portal-read/validate-exit.sh)
- [run-7-portal-read/report.md](./run-7-portal-read/report.md) § Deviations 1
- Deliberately not in [design.md](./design.md): this is a process decision about a
  run's gate script, not a decision about what vocalize does.

---

### DEC-018: Any local process can close the portal with five `Origin`-less POSTs

**Date**: 2026-09-03
**Decided by**: run 7 fix round, on the adversarial review's residual list
**Status**: Decided — accepted limitation

**Context**: DEC-016 refuses a request whose `Origin` is *present and wrong*, and lets
an **absent** `Origin` through on purpose: that is what `curl`, the portal's own smoke
tests and every non-browser client send, and a browser cannot forge an absent `Origin`
on a cross-origin request. The gap that leaves is not a browser one. The listening
socket is on `127.0.0.1`, which every process on the Mac can reach **regardless of
which unix user owns it**, and `POST /api/session` needs no token. So any local
process can send five `Origin`-less POSTs carrying garbage — no code, no token, no
knowledge of anything — and DEC-015's counter closes the owner's portal.

The blast radius is availability and nothing else. Every route that reads or writes
sits behind the token gate, so nothing is read, nothing is written and no key or
config value leaks. The owner sees the portal close and runs `vocalize portal` again,
which mints a fresh code on a fresh port.

| Option | Description | Trade-offs |
|---|---|---|
| A | Accept it and write it down | Costs nothing, and against the attacker who matters most — a process running as the owner — the boundary buys nothing, because that process can `kill` the portal, read `config.toml` and reach the keychain anyway. Leaves a different-user process a nuisance it would not otherwise have |
| B | A timed cooldown instead of a shutdown | The fifth failure refuses further exchanges for a fixed interval rather than closing the server, so a spammer costs the owner a wait rather than a restart. Keeps DEC-015's "count every failure" property intact — still no replay-vs-attack state. But it adds a clock and one more piece of state to the auth path, and a determined spammer can hold the portal in cooldown indefinitely, which is the same denial in a slower coat |
| C | Require a correct `Origin` on every request (DEC-016 option A) | Does not help here at all: a local process sends whatever `Origin` string it likes, including the right one. It would only break `curl` |

**Recommendation**: A, with B named for later.

**Decision**: A. Recorded, not fixed. Vocalize's stated model is a single-user
personal Mac (DEC-004), and on that machine the boundary this would defend is one the
attacker is already inside. **B is the alternative that was not taken** — a timed
cooldown in place of the shutdown — and it is the right first move if this is ever
revisited, because it removes the permanent effect without reintroducing the state
DEC-015 exists to avoid. It is deliberately not implemented on this branch.

**Consequences**: On a multi-user Mac, a process belonging to another user can close
this user's portal at will and repeatedly. That is nuisance, not compromise. Nothing
in run 8 or run 9 should be built assuming the portal cannot be closed under it: the
page already has to survive the server going away, because DEC-015's own five-reload
case and the 15-minute idle watchdog both produce the same outcome. Revisit if the
portal ever becomes long-lived, is bound to anything but loopback, or ships on a
machine with more than one human on it.

**Applied to**:
- Nothing in `vocalize/` — this decision changes no code
- [design.md](./design.md) § Portal routes, in the DEC-016 paragraph
- Not in `verification.md`: there is no behaviour here to verify that DEC-015's and
  DEC-016's own tests do not already cover

---

## Round 7

Run 8's own exit gate, on the same narrow ground DEC-017 opened and no wider.

### DEC-019: Run 8's gate names a branch that does not exist, and times out inside its own suite

**Date**: 2026-09-03
**Decided by**: run 8 close-out, on the adversarial review's residual list
**Status**: Decided

**Context**: Two independent faults in
[run-8-portal-write/validate-exit.sh](./run-8-portal-write/validate-exit.sh), both
of which make the gate report something other than the branch's state.

**The branch name.** The entry check is `on branch config-portal`, matching the
literal string. Run 8 was built on `portal-writes` — the parent's own worktree
setup, and the right call, because run 7's `config-portal` is merged and gone. So
the check failed on every commit run 8 ever made and the gate has never passed as
written. The isolation criterion behind it is met and always was: the work is on
its own branch, forked from a commit that already carries run 7's portal module.

**The timeout.** `CHECK_TIMEOUT` defaults to 120s. Two of the eleven checks run the
whole suite, which takes 115-121s on this machine. The cap sits *inside* that
spread, so "suite green at entry" and "full suite green" were a coin flip between
`PASS` and `FAIL: … (timed out after 120s)` — and a timeout is indistinguishable in
the output from a hung command, which is exactly the failure mode the template's
own header warns about. The previous executor worked around it by exporting
`CHECK_TIMEOUT=600` and said so in its report; a workaround every future runner has
to know about is a defect in the script.

| Option | Description | Trade-offs |
|---|---|---|
| A | Rename the branch to `config-portal` | Makes the literal check pass without touching the script. But it renames live work to satisfy a string, and `config-portal` is run 7's merged branch name — reusing it invites exactly the confusion the separate name avoids |
| B | Delete the check | Smallest diff; but isolation is a real entry criterion. Run 8 mutates config files, and "not on main" is the guard that keeps a half-built write path off `main` |
| C | Check the substance: on a branch that is not `main`, forked from a base carrying `vocalize/portal.py` | Keeps the criterion, drops the accident. Two facts a rename cannot fake, and it survives the next run picking its own branch name |

**Recommendation**: C for the name, and raise the timeout default for the second
fault — they are separate faults in one file, decided together because they are one
edit and one commit.

**Decision**: C, plus `CHECK_TIMEOUT` defaulting to 600, both declared here rather
than changed quietly. The check now asserts `git branch --show-current` is neither
empty (a detached HEAD is not isolation, it is an accident waiting to be lost) nor
`main`, and that `git merge-base main HEAD` names a commit whose tree contains
`vocalize/portal.py` — which is run 7's whole artifact, so the base cannot predate
run 7. Proved four ways in a scratch repository before use: green on a branch off a
base carrying `portal.py`; red sitting on `main`; red on a branch off a base without
`portal.py`; red on a detached HEAD. The timeout was raised to six times the suite's
own runtime rather than to something merely larger than 121s, so an ordinary slow
machine does not put it back on the knife edge.

**Consequences**: DEC-017's ground was "the check cannot pass on any commit, so it
tested nothing". That is exactly true of the branch-name check here and it is why
this amendment is legitimate; it is **not** true of the timeout, which could pass —
about half the time. So the two halves rest on different arguments, and the timeout
half is worth being plain about: raising a cap so a check stops failing is normally
the wrong move, and it is only right here because the thing being measured (does the
suite pass) was never the thing being reported (did the suite finish inside an
arbitrary 120s). Nothing else in the gate changed; no other run's
`validate-exit.sh` was touched, and the 120s default in the other nine remains theirs
to raise if their own suites grow into it — run 10's release gate is the one to
watch, because it runs the same suite.

**Applied to**:
- [run-8-portal-write/validate-exit.sh](./run-8-portal-write/validate-exit.sh)
- [run-8-portal-write/report.md](./run-8-portal-write/report.md) § Deviations
- Deliberately not in [design.md](./design.md) or
  [verification.md](./verification.md): like DEC-017, this is a process decision
  about a run's gate script, not a decision about what vocalize does
