# Decisions: intended mode for dictation

Continues the sequence from [../2026-09-app-roadmap/decisions.md](../2026-09-app-roadmap/decisions.md) (DEC-020 to DEC-037).

The owner decided the goal's scope, speed over RAM, and the undo. On 2026-09-26 the owner said "I'll take your rec on other questions", so Claude decided the rest on its own recommendation. Each entry names its decider, and each carries its reasoning so a wrong call can be caught afterwards. Evidence is in [spike-notes.md](spike-notes.md) and [critique-round-1.md](critique-round-1.md).

| # | Question | Status | Decision | Round |
|---|---|---|---|---|
| DEC-038 | How do the models get warm? | Decided | A — warm socket servers with today's one-shot path as the fallback | R1 |
| DEC-039 | Which cleanup prompt? | Decided | P2 (rules plus worked examples), data rule first, a question example added, for every backend | R1 |
| DEC-040 | What does whisper get as a prompt? | Decided | A vocabulary list only, from `[stt] vocabulary`; never a sentence | R1 |
| DEC-041 | Guard, gate, both or neither? | Decided | The added-content guard ships; the skip gate does not | R1 |
| DEC-042 | How does the undo work? | Decided | A (the owner) — two clipboard types, swapped by `vocalize dictate --swap` from a Quick Action | R1 |
| DEC-043 | What are the new settings called, and their defaults? | Decided | `[stt] warm_minutes` (0 to 240, default 0) and `[stt] vocabulary` (list, default empty) | R1 |
| DEC-044 | What is the warm-server lifecycle? | Decided | Leases per take, idle timer from the last release, flock-owned spawn, one total deadline, a fingerprint handshake | R1 |
| DEC-045 | May a cancelled take still reach the clipboard? | Decided | No — delivery is authorised against the session nonce first | R1 |
| DEC-046 | Which paths warm which models? | Decided | Whisper on every dictation and `listen` start; the LLM only when `cleanup = "local"` and installed; notes stay one-shot | R1 |
| DEC-047 | What memory and speed does a release have to meet? | Decided | Physical footprint, not RSS: LLM at most 3.6 GB, whisper at most 1.0 GB; warm wait after stop at most 5.0 s and at least 1.5 s faster than one-shot | R1 |
| DEC-048 | Where is the control-token defect fixed? | Decided | Pass token ids to `generate` directly; the plan's first task unless a separate fix merges first | R1 |
| DEC-049 | Whom do the warm servers trust? | Decided | The user's own uid only; same-uid processes are out of scope, stated in the docs | R2 |

---

## Round 1

### DEC-038: How do the models get warm?

**Question**: How do the whisper and cleanup models get loaded while the user talks, and stay warm for the next take?

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority
**Status**: Decided

**Context**: A press with local cleanup costs about 7 to 8 s after the stop today, and warm servers measured about 4 s (spike-notes § S3). Most of the difference is uv start, import and load. The owner's rule is that speed beats RAM. Today every press is a fresh `uv run` per worker (DEC-028), and the menu-bar app is frozen (DEC-032).

| Option | Description | Trade-offs |
|---|---|---|
| A | Each worker gains a `--serve` mode on a Unix socket. The start press spawns it, and `transcribe` and `llm._local` try it first, falling back to today's one-shot run | The smallest change to the press state machine, and `listen` benefits too. Adds a local socket boundary and a process lifecycle to get right |
| C | A per-take finisher process owns the workers on pipes. The stop press only writes the stop file | No sockets. But it rewires who delivers the take, and the stop file cannot tell stop from pause or cancel (critique finding 4). Adoption of the next take is undesigned |
| Combined | One process running both runtimes | About 1.9 GB in one process, two heavy runtimes in one environment, and no faster than A |

**Recommendation**: A. Codex's round-1 verdict agreed, on two conditions: a complete lifecycle and cancellation protocol, and release gates for memory, prompt and clipboard privacy.

**Decision**: A, with the conditions carried into DEC-044, DEC-045 and DEC-047 and into the release gates in [verification.md](verification.md).

**Consequences**: Two new long-lived processes exist for the warm window. Today's one-shot path stays as the floor, so any warm failure costs speed, not a take. A protocol file (`vocalize/local/warm_protocol.py`, stdlib only) is shared by both workers and the client. It imports nothing from vocalize, so the rule that workers never import vocalize still holds.

**Applied to**:
- [design.md](design.md) § Approach, § Structure, § Key flows
- [plan.md](plan.md) § Phase 3

---

### DEC-039: Which cleanup prompt?

**Question**: Which system prompt produces intended text?

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority
**Status**: Decided

**Context**: Today's prompt kept the corrected-away word every time in the first spike, and passed 13 of 26 cases in S2. S2's P2 passed 21 of 26. Its remaining failures were two ordinals, two dropped words, and meta-commentary on a dictated question.

| Option | Description | Trade-offs |
|---|---|---|
| P1 | Six numbered rules | 20/26; strips meaningful hedges |
| P2 | P1 plus three worked examples | 21/26; about 0.3 s slower per generate |
| P2 revised | P2 with the data rule moved to rule 1 and a fourth example that is a dictated question left as it is | Targets the one failure that added text; untested until the build's eval |

**Recommendation**: P2 revised, because the question case is the only failure that added text. The eval gate in verification.md must show it no worse than P2 before release.

**Decision**: P2 revised, used for every cleanup backend (local, `claude-cli`, `anthropic`). `VERBATIM_PROMPT` and the spoken "verbatim" keyword are unchanged. `_complete` still appends `DATA_BOUNDARY`, so the prompt constant does not repeat it.

**Consequences**: Cloud-cleanup users get the new prompt too. The summary's non-goal "cloud cleanup keeps working as it does" is narrowed to "no new cloud behaviour besides the shared prompt". The case set moves into the repo as an eval fixture.

**Applied to**:
- [design.md](design.md) § Contracts
- [plan.md](plan.md) § Phase 1

---

### DEC-040: What does whisper get as a prompt?

**Question**: Should whisper get an initial prompt, and what may it contain?

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority
**Status**: Decided

**Context**: In S1 a vocabulary list raised the jargon score to 11 and 12 out of 12, and never leaked. Every variant containing a natural sentence leaked that sentence onto silence or noise on one of two runs.

| Option | Description | Trade-offs |
|---|---|---|
| None | No prompt | 8.5 to 10.5 of 12 on jargon |
| Vocabulary | "Vocabulary: a, b, c." from config | Best jargon result, and no leak seen in S1's small sample |
| Style sentence | A clean sentence to steer style | Leaked a whole sentence onto silence and noise |

**Recommendation**: Vocabulary only. A larger leak run gates the release.

**Decision**: Vocabulary only, built as `Vocabulary: ` plus the list joined with ", " plus ".", and passed explicitly on every call, with `""` when the list is empty (pywhispercpp keeps params between calls). The one-shot fallback passes it as `--initial-prompt`, because a vocabulary is configuration, not a transcript.

**Consequences**: The list is visible in `ps` while a one-shot worker runs. Anything confidential should stay out of the list, and docs/dictation.md says so.

**Applied to**:
- [design.md](design.md) § Contracts
- [plan.md](plan.md) § Phase 2

---

### DEC-041: Guard, gate, both or neither?

**Question**: Which deterministic checks wrap the cleanup call?

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority
**Status**: Decided

**Context**: In S2 the guard never passed added content, and caught the meta-commentary. The gate skipped one take that needed cleaning, a spoken email.

| Option | Description | Trade-offs |
|---|---|---|
| Guard only | Fall back to the raw take when the cleaned text holds a word the raw take lacks (numbers, `@` and `.` mapped from spoken forms) | Catches additions. Cannot see a dropped word or an unapplied correction; the undo covers those |
| Gate only | Skip the model when there is nothing to clean | Saves about 1.2 s on clean takes. Wrong on formatting-only takes |
| Both | Both | Speed from the gate and safety from the guard, plus the gate's wrong skips |

**Recommendation**: Guard only. With warm models a clean take costs about a second of cleanup, and the owner's rule trades RAM for speed, not correctness for speed.

**Decision**: Guard only, under 60 lines, in `llm.py`, applied to every backend's output. Round 2 (C5) adds one rule: every negation in the raw take ("not", "never", "no longer", any word ending in "n't") must survive, unless a correction marker is present. The docs call it an addition check, not injection prevention. A guard fallback prints `vocalize: cleanup skipped (unfaithful)` to stderr and uses the notification that says cleanup was skipped.

**Consequences**: Clean takes still pay a warm generate. Add a gate only if daily use says the wait matters and a gate passes the full case set with no wrong skip.

**Applied to**:
- [design.md](design.md) § Contracts
- [plan.md](plan.md) § Phase 1

---

### DEC-042: How does the undo work?

**Question**: How is the raw take kept one keystroke away without storing it?

**Date**: 2026-09-26
**Decided by**: the owner (option A); the mechanics by Claude
**Status**: Decided

**Context**: DEC-007 promises a transcript is never a file, an argument, a log line or a notification. The app is frozen (DEC-032), and its paste watcher only pastes a marker tied to a watched dictation.

| Option | Description | Trade-offs |
|---|---|---|
| A | The clipboard holds both: cleaned text as plain text, raw take as a private type | No file and no app edit. Gone at the next copy. A clipboard manager that saves every type may keep it |
| B | Raw take in a short-lived 0600 file | Breaks DEC-007 |
| C | No undo; say "verbatim" and dictate again | Nothing to build; not one keystroke |

**Recommendation**: A.

**Decision**: A. When cleanup changed the text, delivery writes two types in one pasteboard write. `public.utf8-plain-text` gets the cleaned text, and `io.github.vocalize-cli.said` gets the raw take. A fixed JavaScript file shipped in the package runs under `/usr/bin/osascript -l JavaScript`, with one JSON object on stdin. No text is ever interpolated into script source. Both texts go through `sanitize` and `_one_line` before the write. `vocalize dictate --swap` swaps the two types, and a second press swaps them back. It acts only when the private type is present and the plain text equals one of the pair; otherwise it does nothing and says so with a fixed notification. A new Quick Action, "Swap in What I Said", runs it. The user then undoes the paste and pastes again. When cleanup did not change the text, delivery uses `pbcopy` exactly as today.

**Consequences**: The undo is a shortcut plus undo plus paste, not one key. The privacy section of docs/dictation.md gains the clipboard line. It says that any app reading the clipboard can read the private type, that a clipboard manager may keep it, and that the next copy replaces the pair but does not guarantee erasure. It also names the existing `claude-cli` history exception. Round 2 (C8): the pair is not authenticated, so another app could forge one. The swap re-sanitises both types and checks the pasteboard change count before and after; it guarantees nothing beyond that. A raw-undo off switch was rejected (C2): cleanup is opt-in, and with cleanup off the same raw text is plain clipboard text today.

**Applied to**:
- [design.md](design.md) § Key flows, § Contracts
- [plan.md](plan.md) § Phase 4

---

### DEC-043: What are the new settings called, and their defaults?

**Question**: The names, types, bounds and defaults of the new `[stt]` keys.

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority
**Status**: Decided

**Context**: `[stt]` rejects unknown keys, so a key is a public contract. An older vocalize refuses a config that uses it.

| Option | Description | Trade-offs |
|---|---|---|
| A | `warm_minutes` (int) and `vocabulary` (list of strings) | Two plain keys that say what they do |
| B | A named profile such as `speed = "lean" \| "fast"` | Friendlier, but hides the number users would tune |

**Recommendation**: A.

**Decision**: A. `warm_minutes` is an integer from 0 to 240, default 0; 0 means unload 10 s after the take ends (round 2, G3), so an immediate second take is still warm. `vocabulary` is a list of at most 50 entries, default empty. Each entry is 1 to 40 characters and 1 to 4 words, drawn only from letters, digits, spaces and `_ . - + / @ # :`, with no `<`, `|` or sentence-ending punctuation (round 2, C6). The built prompt is capped at 800 characters. Both are added to `KNOWN_STT_KEYS`, `_validate_stt_table`, the portal's `[stt]` form and docs/dictation.md.

**Consequences**: A config with either key fails on 0.14.0 and older. The owner's own config gets `cleanup = "local"`, `warm_minutes = 15` and their list, applied by the owner at release.

**Applied to**:
- [design.md](design.md) § Contracts
- [plan.md](plan.md) § Phase 2, § Phase 3

---

### DEC-044: What is the warm-server lifecycle?

**Question**: How are warm servers started, kept, reached and stopped without races, orphans or stale code?

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority
**Status**: Decided

**Context**: Critique findings 6, 7, 8 and 10 showed that an idle timer alone is not a take-shaped contract. Ping-then-spawn races, and a worker-file hash misses runtime and model changes.

| Option | Description | Trade-offs |
|---|---|---|
| A | Idle timer only | Expires under a long recording; no exit trigger when no request comes |
| B | Leases per take, plus the idle timer counted from the last lease release | Survives long takes and ends cleanly on every outcome |

**Recommendation**: B.

**Decision**: B, with these rules:
- **Place.** `~/.cache/vocalize/warm/`, a 0700 directory checked with the same owner and mode test as the cache dir. One socket per model kind, 0600. The server checks the peer uid with `LOCAL_PEERCRED`. The client checks that the socket path is a socket owned by the user, with no symlink.
- **Spawn.** An exclusive `flock` on `warm/<kind>.lock` decides who spawns. The server is started detached (`start_new_session`), with stdin, stdout and stderr on `/dev/null`, and with the offline environment variables for the LLM.
- **Handshake.** Each connection starts with a hello. The server answers its fingerprint: protocol version, sha256 of the worker file, the runtime pin, and the model file's name and size. A mismatch makes the client send `shutdown` and respawn under the lock.
- **Leases.** `lease <id>` at take start; `release <id>` on every outcome (delivered, cancelled, silent, failed). An unreleased lease expires after `max_take_seconds` plus 60 s. The idle timer runs only while no lease is held. It is `warm_minutes`, or 10 s when that is 0.
- **Requests.** One at a time, with a 64 KB cap on a request line and one JSON object per line. Fixed error codes only: `busy`, `loading`, `bad-request`, `failed`. No text from a request ever reaches stderr or a log.
- **Robustness, from S3.** A server survives a client that connects and vanishes (`BrokenPipeError`, `ConnectionResetError`). Lifecycle control goes through the socket (`shutdown`), never a PID, because `uv run` does not replace itself and the spawned PID is uv's. The client refuses a socket path over 100 bytes and falls back.
- **Round 2 limits (C3, C4, G4).** Each connection has 2 s to deliver a full request line. `max_tokens` is at most 1,024, replies are capped at 64 KB, at most 8 leases are held, and one connection is served at a time. The whisper server opens a WAV once (`O_NOFOLLOW | O_NONBLOCK`), requires a regular file under 200 MB, validates the format on that handle, and transcribes the frames as an array, never the path. The LLM server uses `stream_generate` and checks its deadline and a cancel flag between tokens. Both run a watchdog thread that calls `os._exit` when a request outlives its deadline by 5 s. `no_context=True` and `initial_prompt` are passed on every whisper call. The LLM keeps no prompt cache. `cancel` names the request id it cancels (C1).
- **Start never waits (G2).** Warming runs after the recorder is launched. A new server is spawned with `--lease <id>`, so no handshake is needed. A lease on a live server gets 200 ms, then is skipped.
- **Deadline.** The client allows one total deadline per request. A `loading` reply is waited on, not treated as failure. If the model has not answered by the deadline, the client sends `shutdown` before falling back, so two copies of a model never run at once (G1).

**Consequences**: The workers grow a small server loop each. The protocol file carries the shared framing and the fingerprint. Tests use stub models behind the existing seams.

**Applied to**:
- [design.md](design.md) § Contracts, § Key flows
- [plan.md](plan.md) § Phase 3

---

### DEC-045: May a cancelled take still reach the clipboard?

**Question**: A cancel during transcription releases the session, but the transcribing process still copies its text (dictate.py `cancel`, then `_stop`'s `copy_to_clipboard`). Should it?

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority
**Status**: Decided

**Context**: This is a shipped behaviour, found in critique finding 3 and confirmed by reading `_stop` and `cancel`. Cleanup makes the window longer.

| Option | Description | Trade-offs |
|---|---|---|
| A | Leave it | A cancelled take can overwrite whatever the user copied next |
| B | Check the session nonce right before delivery | A cancelled take never copies, marks or notifies success |

**Recommendation**: B.

**Decision**: B. `_stop` takes an exclusive `flock` on the session file, re-reads the nonce, writes the clipboard, then drops the lock. `cancel` takes the same lock before it releases the session, so a cancel can never land between the check and the write (round 2, C9). If this take no longer owns the session, it discards the text, and releases its leases on the way out.

**Consequences**: One more check on the delivery path, with a test that cancels between transcription and delivery.

**Applied to**:
- [plan.md](plan.md) § Phase 0
- vocalize PR #16, squash 8dd4f61 (2026-09-26): ids to `generate`, red test 3aaa7da first

---

### DEC-046: Which paths warm which models?

**Question**: What starts a warm server, and who uses one?

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority
**Status**: Decided

**Context**: Critique finding 12. Users with cleanup off should not load an LLM. `vocalize notes` shares the `_local` seam but runs long jobs with different limits.

**Decision**: as follows.
- The dictation start (toggle, hold-to-talk, and resume after pause) and `vocalize listen`'s own recording start warm whisper. They warm the LLM only when `cleanup = "local"` and the model is installed.
- `listen --wav` warms nothing, but uses a warm server if one is up.
- `vocalize notes` never uses a warm server. It keeps the one-shot path.
- A warm server never makes a network call. The LLM server is spawned with the same offline variables as today.

| Option | Description | Trade-offs |
|---|---|---|
| A | As above | Scoped to dictation, as the goal says |
| B | Also warm for notes | Notes jobs run for minutes, with different limits; out of scope |

**Recommendation**: A.

**Consequences**: `llm._local` takes a flag saying whether it may use the warm server. `summarize` passes no.

**Applied to**:
- [design.md](design.md) § Structure
- [plan.md](plan.md) § Phase 3

---

### DEC-047: What memory and speed does a release have to meet?

**Question**: The summary's constraint says each worker stays under 1.5 GB. S3 measured the cleanup model at 1.3 GB RSS but 3.3 GB physical footprint. What ceiling holds, and what speed must warm servers prove?

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority (the owner's rule: speed beats RAM)
**Status**: Decided

**Context**: The 1.5 GB cap came from RSS readings (roadmap spike-notes § LLM). mlx's unified memory is mostly invisible to RSS, and today's one-shot worker already has the same 3.3 GB footprint while it runs. What warm servers change is how long that memory is held: the warm window instead of a few seconds. Warm saves about 2 to 4 s a take (spike-notes § S3).

| Option | Description | Trade-offs |
|---|---|---|
| A | Keep the 1.5 GB RSS cap | Passes on paper, and measures the wrong thing for mlx |
| B | Footprint ceilings at the measured peak plus about 10%, plus speed gates | Honest numbers. Holds about 4 GB for the warm window, on a machine the installer already requires to have 12 GiB |
| C | A smaller cleanup model to fit 1.5 GB | Needs a download and a new quality run; S2 showed the 4B model is only just good enough |

**Recommendation**: B, per the owner's rule.

**Decision**: B. A release needs, on the reference Mac, with `vmmap --summary` on the model process (the child of `uv`, not `uv` itself):
- LLM server physical footprint peak at most 3.6 GB
- whisper server physical footprint peak at most 1.0 GB
- both at once at most 4.6 GB
- warm wait after the stop, S1 jargon clip, 3 s and 10 s takes: at most 5.0 s each
- back-to-back warm takes: at most 4.5 s
- warm at least 1.5 s faster than the one-shot path measured in the same run

The summary's "under 1.5 GB per worker" is replaced by these. `llm_manifest.MIN_RAM_BYTES` (12 GiB) stays.

**Consequences**: With `warm_minutes = 15` about 4 GB stays held for up to 15 minutes after a take. docs/dictation.md states the footprint, not the RSS. If a gate fails, the phase stops and the gate's numbers come back to the owner.

**Applied to**:
- [design.md](design.md) § Memory
- [verification.md](verification.md) § Phase 3 exit
- [plan.md](plan.md) § Phase 3, T-36

---

### DEC-049: Whom do the warm servers trust?

**Question**: Codex round 2 (C1) showed that a process running as the same user can replace a socket, answer with a valid fingerprint, or send `shutdown`. Is that in scope?

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority
**Status**: Decided

**Context**: Every transcript ends on the general clipboard, which any same-user process can read. Same-user processes can also read `~/.cache/vocalize` and the config. macOS offers no cheap way to authenticate a peer process beyond its uid.

| Option | Description | Trade-offs |
|---|---|---|
| A | Same uid is trusted; other uids are refused | Honest about what the platform gives. Matches the clipboard's exposure |
| B | An authenticated broker with verified process identity | Real work (code-signing checks on an ad-hoc-signed toolchain), guarding text that reaches the clipboard anyway |

**Recommendation**: A.

**Decision**: A. The server checks the peer uid, and the client checks the socket's owner, type and mode and refuses symlinks. `cancel` names its request id. docs/dictation.md states the boundary: another program running as you can talk to the warm servers, as it can already read your clipboard.

**Consequences**: No broker. If vocalize ever runs a server for another user, or on a shared machine, this entry is revisited.

**Applied to**:
- [design.md](design.md) § Contracts
- [plan.md](plan.md) § Phase 3, T-30, T-43

---

### DEC-048: Where is the control-token defect fixed?

**Question**: `llm_worker._generate` decodes prompt ids to a string that `mlx_lm.generate` re-encodes, re-creating control tokens (spike-notes § Control tokens). Where does the fix go?

**Date**: 2026-09-26
**Decided by**: Claude, on the owner's standing authority
**Status**: Decided

| Option | Description | Trade-offs |
|---|---|---|
| A | A separate fix-defect session now | Ships the fix without waiting for this plan |
| B | Only inside this plan | Leaves 0.14.0 exposed until this plan ships |

**Recommendation**: A, with this plan's T-01 as the backstop.

**Decision**: A separate task was flagged on 2026-09-26. T-01 does the fix here only if that fix has not merged by the time Phase 0 starts. Either way, `generate` receives the id list, and a test proves a literal `<|im_end|>` in user text never becomes the control id.

**Consequences**: Phase 0's entry criterion checks main for the fix first.

**Applied to**:
- [plan.md](plan.md) § Phase 0
